"""AST node classes for the expression language.

The grammar is tiny on purpose:
  - boolean: AND, OR, NOT
  - comparison: > < >= <= == !=
  - arithmetic: + - * / and unary minus
  - leaves: identifiers, numbers, parenthesised exprs

No string ops, no function calls, no SQL injection surface — identifiers are
validated against the registry, numbers go through parameter binding.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class Expr:
    """Base class — purely a type marker."""
    pass


@dataclass(frozen=True)
class BoolOp(Expr):
    op: Literal["AND", "OR"]
    operands: tuple["Expr", ...]   # 2+ operands; we left-associate during parsing


@dataclass(frozen=True)
class Not(Expr):
    operand: Expr


@dataclass(frozen=True)
class Compare(Expr):
    op: Literal[">", "<", ">=", "<=", "==", "!="]
    left: Expr
    right: Expr


@dataclass(frozen=True)
class BinOp(Expr):
    op: Literal["+", "-", "*", "/"]
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Neg(Expr):
    operand: Expr


@dataclass(frozen=True)
class Number(Expr):
    value: float


@dataclass(frozen=True)
class Ident(Expr):
    name: str


@dataclass(frozen=True)
class Func(Expr):
    """A cross-sectional transform over the universe at date T — compiled to a
    SQL window function. ``rank`` / ``decile`` / ``percentrank`` / ``zscore``
    (1 arg) and ``quantile(x, n)`` (n = integer literal). Lets a screen RANK,
    not just threshold: e.g. ``decile(roe) == 10`` is the top-decile-ROE names."""
    name: str
    args: tuple["Expr", ...]
