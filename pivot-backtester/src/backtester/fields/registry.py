"""Field registry — the dictionary that maps user-facing names to data lookups.

Loads `base_fields.yaml` and `computed_fields.yaml` at construction. Validates
that every identifier referenced by a computed expression resolves, and that
the computed graph has no cycles.

The registry deliberately knows nothing about SQL. It produces `FieldSpec`
objects; the compiler turns those into queries.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml


_THIS_DIR = Path(__file__).resolve().parent
_BASE_FIELDS_YAML = _THIS_DIR / "base_fields.yaml"
_COMPUTED_FIELDS_YAML = _THIS_DIR / "computed_fields.yaml"

# Identifiers found inside a computed expression. Same shape as the lark
# IDENT terminal: lowercase, digits, underscore. The negative lookbehind
# prevents the `e7` of a scientific-notation literal like `1e7` or `1.5e10`
# from being mis-extracted as an identifier.
_IDENT_RE = re.compile(r"(?<![0-9.])[a-z_][a-z0-9_]*")
# Tiny set of reserved tokens that look like idents but are operators.
_OP_KEYWORDS = {"AND", "OR", "NOT", "and", "or", "not"}


class UnknownFieldError(KeyError):
    """Raised for a field name that does not resolve."""

    def __init__(self, name: str, suggestions: list[str] | None = None) -> None:
        self.name = name
        self.suggestions = suggestions or []
        msg = f"Unknown field: {name!r}"
        if suggestions:
            msg += f". Did you mean: {', '.join(repr(s) for s in suggestions)}?"
        super().__init__(msg)


class CircularReferenceError(ValueError):
    """A computed field expression references itself transitively."""


# ---- Field specs --------------------------------------------------------


@dataclass(frozen=True)
class BaseFieldSpec:
    """A leaf field — single statement_lines lookup."""
    name: str
    statement: str
    basis_default: str
    line_items: tuple[str, ...]
    ttm: bool = False                 # True when resolved with the _ttm suffix
    ttm_eligible: bool = False        # whether _ttm is allowed for this base
    unit: str = "ratio"
    description: str = ""

    @property
    def kind(self) -> str:
        return "base"


@dataclass(frozen=True)
class ComputedFieldSpec:
    """A derived field — its expression is parsed and substituted by the compiler."""
    name: str
    expr_text: str
    unit: str = "ratio"
    description: str = ""

    @property
    def kind(self) -> str:
        return "computed"


@dataclass(frozen=True)
class PriceFieldSpec:
    """The `price` leaf — resolves to the latest adjusted close from mc.daily_prices."""
    name: str = "price"
    unit: str = "rupees"

    @property
    def kind(self) -> str:
        return "price"


FieldSpec = BaseFieldSpec | ComputedFieldSpec | PriceFieldSpec


# ---- Registry -----------------------------------------------------------


@dataclass
class Registry:
    base: dict[str, BaseFieldSpec] = field(default_factory=dict)
    computed: dict[str, ComputedFieldSpec] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, base_path: Path, computed_path: Path) -> "Registry":
        base = _load_base_fields(base_path)
        computed = _load_computed_fields(computed_path)
        reg = cls(base=base, computed=computed)
        reg._validate()
        return reg

    # --- public API ------------------------------------------------------

    def lookup(self, name: str) -> FieldSpec:
        """Resolve an identifier to a FieldSpec.

        Handles the `_ttm` suffix on base fields whose `ttm_eligible` flag is set.
        Unknown names raise `UnknownFieldError` with did-you-mean suggestions.
        """
        if name == "price":
            return PriceFieldSpec()
        if name in self.computed:
            return self.computed[name]
        if name in self.base:
            return self.base[name]
        if name.endswith("_ttm"):
            stem = name[: -len("_ttm")]
            if stem in self.base:
                spec = self.base[stem]
                if not spec.ttm_eligible:
                    raise UnknownFieldError(
                        name,
                        suggestions=[stem],
                    )
                # Synthesise the TTM variant.
                return BaseFieldSpec(
                    name=name,
                    statement=spec.statement,
                    basis_default=spec.basis_default,
                    line_items=spec.line_items,
                    ttm=True,
                    ttm_eligible=True,
                    unit=spec.unit,
                    description=f"TTM (last 4 quarters) of {stem}",
                )
        raise UnknownFieldError(name, suggestions=self._suggest(name))

    def all_names(self) -> list[str]:
        names = ["price"]
        names.extend(sorted(self.base.keys()))
        names.extend(sorted(self.computed.keys()))
        ttm_names = [f"{n}_ttm" for n, s in self.base.items() if s.ttm_eligible]
        names.extend(sorted(ttm_names))
        return names

    # --- internals -------------------------------------------------------

    def _suggest(self, name: str) -> list[str]:
        return difflib.get_close_matches(name, self.all_names(), n=3, cutoff=0.6)

    def _validate(self) -> None:
        """Validate every computed expression and check for cycles."""
        # 1. Every ident in a computed expr must resolve (statically — without
        #    pulling in the parser, since registry sits below the parser).
        for name, spec in self.computed.items():
            for ident in extract_identifiers(spec.expr_text):
                # `lookup` raises if unknown; that's the validation.
                self.lookup(ident)

        # 2. No cycles.
        for name in self.computed:
            self._check_no_cycle(name, ancestors=())

    def _check_no_cycle(self, name: str, ancestors: tuple[str, ...]) -> None:
        if name in ancestors:
            chain = " -> ".join(ancestors + (name,))
            raise CircularReferenceError(f"Circular reference: {chain}")
        spec = self.computed.get(name)
        if spec is None:
            return
        for ident in extract_identifiers(spec.expr_text):
            stem = ident[: -len("_ttm")] if ident.endswith("_ttm") else ident
            child = self.computed.get(stem) and stem  # only recurse into computed
            if child:
                self._check_no_cycle(child, ancestors + (name,))


# ---- Helpers ------------------------------------------------------------


def extract_identifiers(expr_text: str) -> list[str]:
    """Pull bare identifiers out of an expression string.

    Used during registry validation so we can check refs without loading the parser.
    Skips numeric literals and operator keywords. Order is preserved, duplicates
    removed (first-wins).
    """
    seen: dict[str, None] = {}
    for tok in _IDENT_RE.findall(expr_text):
        if tok in _OP_KEYWORDS:
            continue
        if tok in seen:
            continue
        seen[tok] = None
    return list(seen.keys())


def _load_base_fields(path: Path) -> dict[str, BaseFieldSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, BaseFieldSpec] = {}
    for name, body in raw.items():
        out[name] = BaseFieldSpec(
            name=name,
            statement=body["statement"],
            basis_default=body.get("basis_default", "consolidated"),
            line_items=tuple(body["line_items"]),
            ttm=False,
            ttm_eligible=bool(body.get("ttm_eligible", False)),
            unit=body.get("unit", "ratio"),
            description=body.get("description", ""),
        )
    return out


def _load_computed_fields(path: Path) -> dict[str, ComputedFieldSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, ComputedFieldSpec] = {}
    for name, body in raw.items():
        out[name] = ComputedFieldSpec(
            name=name,
            expr_text=body["expr"],
            unit=body.get("unit", "ratio"),
            description=body.get("description", ""),
        )
    return out


def load_default_registry() -> Registry:
    return Registry.from_yaml(_BASE_FIELDS_YAML, _COMPUTED_FIELDS_YAML)
