"""Validate an AST against a field registry.

Catches:
  - unknown identifiers (with did-you-mean)
  - top-level expression that isn't a boolean predicate
  - unit-mismatch warnings (rupee value compared to a ratio, etc.)
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Iterable

from ..fields import Registry, UnknownFieldError
from .ast import BinOp, BoolOp, Compare, Expr, Func, Ident, Neg, Not, Number

# Cross-sectional functions and their (min, max) argument counts.
_XS_FUNC_ARITY = {
    "rank": (1, 1), "decile": (1, 1), "percentrank": (1, 1),
    "zscore": (1, 1), "quantile": (2, 2),
}


def _contains_func(node: Expr) -> bool:
    if isinstance(node, Func):
        return True
    if isinstance(node, BoolOp):
        return any(_contains_func(o) for o in node.operands)
    if isinstance(node, Not):
        return _contains_func(node.operand)
    if isinstance(node, (Compare, BinOp)):
        return _contains_func(node.left) or _contains_func(node.right)
    if isinstance(node, Neg):
        return _contains_func(node.operand)
    return False


class ValidationError(Exception):
    """User-facing parse / type / lookup error with optional suggestions."""

    def __init__(self, message: str, suggestions: list[str] | None = None) -> None:
        super().__init__(message)
        self.suggestions = suggestions or []


@dataclass
class ValidationResult:
    referenced_fields: list[str]
    warnings: list[str]


def validate(ast: Expr, registry: Registry) -> ValidationResult:
    """Walk the AST, validate every identifier, surface unit warnings."""
    if not isinstance(ast, (BoolOp, Not, Compare)):
        raise ValidationError(
            "Top-level expression must be a boolean predicate "
            "(comparison, AND, OR, NOT). "
            f"Got: {type(ast).__name__}"
        )

    referenced: list[str] = []
    seen: set[str] = set()
    warnings_out: list[str] = []

    def visit(node: Expr) -> str | None:
        """Returns the unit if this subtree resolves to a single field, else None."""
        if isinstance(node, BoolOp):
            for op in node.operands:
                visit(op)
            return None
        if isinstance(node, Not):
            visit(node.operand)
            return None
        if isinstance(node, Compare):
            l_unit = visit(node.left)
            r_unit = visit(node.right)
            if l_unit and r_unit and l_unit != r_unit:
                warnings_out.append(
                    f"Comparison between units {l_unit!r} and {r_unit!r}: "
                    "are you sure?"
                )
            return None
        if isinstance(node, BinOp):
            visit(node.left)
            visit(node.right)
            return None
        if isinstance(node, Neg):
            return visit(node.operand)
        if isinstance(node, Func):
            if node.name not in _XS_FUNC_ARITY:
                raise ValidationError(
                    f"Unknown function {node.name!r}. Cross-sectional functions: "
                    + ", ".join(sorted(_XS_FUNC_ARITY)) + "."
                )
            lo, hi = _XS_FUNC_ARITY[node.name]
            if not (lo <= len(node.args) <= hi):
                raise ValidationError(
                    f"{node.name}() takes "
                    + (f"{lo}" if lo == hi else f"{lo}-{hi}")
                    + f" argument(s), got {len(node.args)}."
                )
            if node.name == "quantile":
                n_arg = node.args[1]
                if (not isinstance(n_arg, Number)
                        or n_arg.value != int(n_arg.value)
                        or not (2 <= int(n_arg.value) <= 100)):
                    raise ValidationError(
                        "quantile(x, n): n must be an integer literal between 2 and 100."
                    )
            if _contains_func(node.args[0]):
                raise ValidationError(
                    f"{node.name}() can't be nested inside another "
                    "cross-sectional function."
                )
            visit(node.args[0])  # validate the value expression (idents resolve)
            return None  # a rank/score is a fresh numeric — no field unit
        if isinstance(node, Number):
            return None
        if isinstance(node, Ident):
            try:
                spec = registry.lookup(node.name)
            except UnknownFieldError as e:
                raise ValidationError(str(e), suggestions=e.suggestions) from None
            if node.name not in seen:
                seen.add(node.name)
                referenced.append(node.name)
            return getattr(spec, "unit", None)
        raise AssertionError(f"unhandled AST node: {type(node)}")

    visit(ast)
    return ValidationResult(referenced_fields=referenced, warnings=warnings_out)
