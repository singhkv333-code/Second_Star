"""Canonical strategy boundary shared by chat, cards, backtests and workflows."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.core.data.intervals import normalize_interval
from backend.workflows.dsl.readback import tree_to_english
from backend.workflows.dsl.schema import Tree, normalize_tree_aliases
from backend.workflows.dsl.validators import semantic_validate
from backend.workflows.propose import validate_draft_against_registry


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstrumentSpec(_Strict):
    symbol: str = Field(min_length=1, max_length=40)
    exchange: Literal["NSE", "BSE", "NFO", "MCX"] = "NSE"
    interval: str = "1d"

    @model_validator(mode="after")
    def normalize(self) -> "InstrumentSpec":
        self.symbol = self.symbol.strip().upper()
        self.interval = normalize_interval(self.interval)
        return self


class OrderSpec(_Strict):
    side: Literal["buy"] = "buy"
    quantity: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    notional_inr: Optional[float] = Field(default=None, gt=0)
    order_type: Literal["market", "limit"] = "market"
    limit_price: Optional[float] = Field(default=None, gt=0)
    product: Literal["CNC", "MIS"] = "CNC"
    requires_approval: bool = True

    @model_validator(mode="after")
    def validate_order(self) -> "OrderSpec":
        if (self.quantity is None) == (self.notional_inr is None):
            raise ValueError("provide exactly one of quantity or notional_inr")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("market orders must not include limit_price")
        return self


class ExitSpec(_Strict):
    kind: Literal["tree", "hold_to_end"] = "tree"
    tree: Optional[Tree] = None
    exit_at: Literal["next_open", "current_close"] = "next_open"

    @field_validator("tree", mode="before")
    @classmethod
    def normalize_tree(cls, value: object) -> object:
        return normalize_tree_aliases(value)

    @model_validator(mode="after")
    def validate_exit(self) -> "ExitSpec":
        if self.kind == "tree" and self.tree is None:
            raise ValueError("tree exit requires tree")
        if self.kind == "hold_to_end" and self.tree is not None:
            raise ValueError("hold_to_end must not include tree")
        return self


class StrategySpec(_Strict):
    """Model-authored intent, without model-authored executable code."""

    spec_version: Literal["1"] = "1"
    revision: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=100)
    instrument: InstrumentSpec
    entry: Tree
    exit: ExitSpec = Field(default_factory=lambda: ExitSpec(kind="hold_to_end"))
    order: OrderSpec
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    notes: Optional[str] = Field(default=None, max_length=1000)


    @field_validator("entry", mode="before")
    @classmethod
    def normalize_entry(cls, value: object) -> object:
        return normalize_tree_aliases(value)


def validate_strategy(spec: StrategySpec) -> tuple[Tree, Optional[Tree]]:
    entry = spec.entry
    semantic_validate(entry)
    exit_tree: Optional[Tree] = None
    if spec.exit.kind == "tree":
        exit_tree = spec.exit.tree
        if exit_tree is None:  # guarded by ExitSpec; keeps this boundary total
            raise ValueError("tree exit requires tree")
        semantic_validate(exit_tree, allow_position=True)
    return entry, exit_tree


def strategy_readback(spec: StrategySpec) -> dict[str, Optional[str]]:
    entry, exit_tree = validate_strategy(spec)
    return {
        "entry": tree_to_english(entry),
        "exit": tree_to_english(exit_tree) if exit_tree is not None else "Hold to end",
    }


def compile_workflow(spec: StrategySpec) -> dict[str, Any]:
    """Compile, validate, but never persist/activate/execute the workflow."""
    entry, exit_tree = validate_strategy(spec)
    symbol = spec.instrument.symbol
    order_config: dict[str, Any] = {
        "symbol": symbol, "side": "buy", "order_type": spec.order.order_type,
        "product": spec.order.product,
        "requires_approval": True,  # register-not-execute invariant
    }
    if spec.order.quantity is not None:
        order_config["quantity"] = spec.order.quantity
    else:
        order_config["notional_inr"] = spec.order.notional_inr
    if spec.order.limit_price is not None:
        order_config["limit_price"] = spec.order.limit_price

    steps: list[dict[str, Any]] = [
        {"step_type": "trigger.compound", "label": "Entry condition",
         "config": {"entry": entry.model_dump(mode="json")}},
        {"step_type": "action.place_order", "label": "Register buy order",
         "config": order_config},
    ]
    if exit_tree is not None:
        portfolio_idx = len(steps) + 1
        steps.extend([
            {"step_type": "trigger.exit_compound", "label": "Exit condition",
             "config": {"entry": exit_tree.model_dump(mode="json"),
                        "target_symbol": symbol}},
            {"step_type": "fetch.portfolio", "label": "Read open position", "config": {}},
            {"step_type": "action.place_order", "label": "Register exit order", "config": {
                "symbol": symbol, "side": "sell",
                "quantity": f"{{{{ context.{portfolio_idx}.holdings.{symbol}.quantity }}}}",
                "order_type": "market", "product": spec.order.product,
                "requires_approval": True,
            }},
        ])
    readback = strategy_readback(spec)
    raw = {
        "name": spec.name,
        "description": f"Entry: {readback['entry']} · Exit: {readback['exit']}",
        "steps": steps,
        "rationale": spec.notes,
        "warnings": ["Draft only. Orders are registered and require user confirmation."],
    }
    draft = validate_draft_against_registry(raw)
    return {"_render_hint": "workflow_draft_card", **draft.model_dump(mode="json")}
