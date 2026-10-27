"""Lark grammar + parser for the expression language."""
from __future__ import annotations

from lark import Lark, Transformer, v_args

from .ast import BinOp, BoolOp, Compare, Expr, Func, Ident, Neg, Not, Number


_GRAMMAR = r"""
start: expr

?expr: or_expr

?or_expr: and_expr (OR and_expr)*
?and_expr: not_expr (AND not_expr)*
?not_expr: NOT not_expr  -> not_op
         | comparison

?comparison: arith (COMP_OP arith)?

?arith: term (ADD_OP term)*
?term: factor (MUL_OP factor)*

?factor: NUMBER          -> number
       | IDENT "(" [arglist] ")"  -> func
       | IDENT            -> ident
       | "(" expr ")"
       | "-" factor       -> neg

arglist: expr ("," expr)*

OR: "OR"i
AND: "AND"i
NOT: "NOT"i

COMP_OP: ">=" | "<=" | "==" | "!=" | ">" | "<"
ADD_OP:  "+" | "-"
MUL_OP:  "*" | "/"

IDENT: /(?!(?:and|or|not)\b)[a-z_][a-z0-9_]*/i
NUMBER: /\d+(\.\d+)?([eE][+-]?\d+)?/

%ignore /[ \t\r\n]+/
"""


class _Builder(Transformer):
    """Lark Transformer → AST nodes."""

    def start(self, items):
        return items[0]

    def number(self, items):
        return Number(float(items[0]))

    def ident(self, items):
        return Ident(str(items[0]))

    @v_args(inline=False)
    def arglist(self, items):
        # Anonymous "," terminals are filtered by Lark; keep only Expr nodes.
        return [it for it in items if isinstance(it, Expr)]

    @v_args(inline=False)
    def func(self, items):
        # items = [IDENT_token] or [IDENT_token, arglist_list].
        name = str(items[0]).lower()
        args = items[1] if len(items) > 1 and isinstance(items[1], list) else []
        return Func(name, tuple(args))

    def neg(self, items):
        return Neg(items[0])

    def not_op(self, items):
        # items = [NOT_token, operand]
        return Not(items[1])

    @v_args(inline=False)
    def or_expr(self, items):
        # items = [a, OR, b, OR, c, ...] when there are ORs; else [a]
        operands = [items[0]]
        for i in range(1, len(items), 2):
            operands.append(items[i + 1])
        if len(operands) == 1:
            return operands[0]
        return BoolOp("OR", tuple(operands))

    @v_args(inline=False)
    def and_expr(self, items):
        operands = [items[0]]
        for i in range(1, len(items), 2):
            operands.append(items[i + 1])
        if len(operands) == 1:
            return operands[0]
        return BoolOp("AND", tuple(operands))

    @v_args(inline=False)
    def comparison(self, items):
        if len(items) == 1:
            return items[0]
        left, op_token, right = items
        return Compare(str(op_token), left, right)

    @v_args(inline=False)
    def arith(self, items):
        node = items[0]
        for i in range(1, len(items), 2):
            op = str(items[i])
            rhs = items[i + 1]
            node = BinOp(op, node, rhs)
        return node

    @v_args(inline=False)
    def term(self, items):
        node = items[0]
        for i in range(1, len(items), 2):
            op = str(items[i])
            rhs = items[i + 1]
            node = BinOp(op, node, rhs)
        return node


_PARSER = Lark(_GRAMMAR, parser="earley", maybe_placeholders=False)
_BUILDER = _Builder()


def parse_expression(text: str) -> Expr:
    """Parse expression source text into an AST.

    Raises `lark.exceptions.LarkError` (or its subclasses, e.g. UnexpectedToken)
    on syntax errors. The validator wraps these into `ValidationError` for the
    user-facing path.
    """
    tree = _PARSER.parse(text)
    return _BUILDER.transform(tree)
