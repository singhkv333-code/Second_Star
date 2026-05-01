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
from .ast import BinOp, BoolOp, Compare, Expr, Ident, Neg, Not, Number


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
