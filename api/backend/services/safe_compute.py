"""safe_compute — the deterministic COMPUTE lane for the chat agent.

Why this exists: the agent was a tool-picker first and a reasoner second.
Any ask that was *computable but untooled* ("percentile-rank these stocks
by ROE") fell through to an honest-sounding refusal, because the
behavioural contract (correctly) bans fabricating market data and the
model over-generalised that ban to arithmetic. The fix is NOT to let the
LLM do mental math (token-predicted arithmetic silently drifts on
anything nontrivial) — it's this: a sandboxed, deterministic evaluator
the model calls with a short Python expression over values ALREADY in
context. Fabrication = inventing data you never saw. Computing over data
you have is not fabrication — and now it's exact.

Two layers of defence, both required:

1.  **AST whitelist (in-process, before any execution).** Only the
    expression-language subset a calculator needs: literals,
    arithmetic/bool/compare ops, comprehensions, lambda, subscripts,
    f-strings, assignments, bounded `for` loops, and calls to
    whitelisted builtins or ``math.*`` / ``statistics.*``. No imports, no
    attribute access (except the two module namespaces), no dunders, no
    `while` (unbounded looping), no defs, no huge literals.

2.  **Isolated subprocess (execution).** Even whitelisted code can be a
    resource bomb ("a"*10**6 repeated, deep lambda recursion), so the
    validated code runs in ``python -I -c`` with CPU/memory rlimits and
    a hard wall-clock timeout. The API worker can never be hung by a
    compute call; worst case the child dies and we return an honest
    error.

The tool contract (see agents/tools.py::compute) requires every input
number to be a literal the model saw in-conversation — the sandbox makes
the arithmetic trustworthy; the contract keeps the inputs honest.
"""
from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Limits ────────────────────────────────────────────────────────────
MAX_CODE_CHARS = 4_000
MAX_AST_NODES = 2_000
MAX_CONST_INT = 10**12          # ₹-crore scale fits; 10**15 bombs don't
MAX_CONST_STR = 10_000
TIMEOUT_S = 3.0
MAX_RESULT_CHARS = 20_000

# Builtin callables the sandbox exposes. `range` is swapped for a
# bounded version inside the runner.
_SAFE_BUILTIN_NAMES: frozenset[str] = frozenset({
    "abs", "round", "min", "max", "sum", "len", "sorted", "reversed",
    "range", "enumerate", "zip", "map", "filter", "any", "all",
    "int", "float", "str", "bool", "list", "dict", "set", "tuple",
    "divmod", "pow",
})

_SAFE_MODULES: frozenset[str] = frozenset({"math", "statistics"})

_ALLOWED_NODES: tuple[type, ...] = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign,
    ast.Name, ast.Load, ast.Store, ast.Constant,
    ast.Tuple, ast.List, ast.Dict, ast.Set,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.If,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    # BitOr doubles as Python's dict-merge operator (`d1 | d2`) — common
    # in basket/weighting code the model writes. Pure arithmetic/merge,
    # no new escape surface; already sandboxed by the same subprocess +
    # rlimits as every other operator here.
    ast.BitOr,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Call, ast.keyword, ast.Attribute,
    ast.Subscript, ast.Slice, ast.Index if hasattr(ast, "Index") else ast.Slice,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    ast.comprehension, ast.Lambda, ast.arguments, ast.arg,
    ast.JoinedStr, ast.FormattedValue, ast.Starred,
    # Bounded `for` loops (basket weighting, running totals, etc). Not a
    # new escape surface — `range()` is already capped at 200k in the
    # runner and the CPU rlimit + wall-clock timeout bound worst-case
    # runtime the same way they already bound comprehensions/lambdas.
    # `while` stays unlisted: an unbounded while can spin the full
    # timeout on trivial code, which `for`-over-range/collection cannot.
    ast.For, ast.Break, ast.Continue,
)


class ComputeValidationError(ValueError):
    """Raised when submitted code falls outside the calculator subset.

    The message is written FOR THE MODEL (it sees tool errors and can
    self-correct in the same conversation), so it names the offending
    construct precisely."""


def validate_code(code: str) -> ast.Module:
    """Whitelist-validate `code`; returns the parsed tree or raises
    ComputeValidationError naming the first offending construct."""
    if not code or not code.strip():
        raise ComputeValidationError("code is empty")
    if len(code) > MAX_CODE_CHARS:
        raise ComputeValidationError(
            f"code too long ({len(code)} chars > {MAX_CODE_CHARS})"
        )
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        raise ComputeValidationError(f"syntax error: {e.msg} (line {e.lineno})") from e

    n_nodes = 0
    for node in ast.walk(tree):
        n_nodes += 1
        if n_nodes > MAX_AST_NODES:
            raise ComputeValidationError("expression too complex")

        if not isinstance(node, _ALLOWED_NODES):
            raise ComputeValidationError(
                f"disallowed construct: {type(node).__name__} — the compute "
                f"sandbox is a calculator (literals, arithmetic, "
                f"comprehensions, sorted/sum/min/max/round, math.*, "
                f"statistics.*). No imports, loops, defs, or I/O."
            )

        # Attribute access: block the escape surface, allow the calculator
        # surface. Every known Python sandbox escape traverses underscore
        # attributes (__class__/__mro__/__globals__/__subclasses__), or
        # uses the two non-underscore gadgets that can reach them:
        # str.format's "{0.__class__}" traversal and type.mro(). Blocking
        # those leaves plain container/str methods (.values, .items,
        # .keys, .get, .split, .join …) — all safe and essential for
        # dict-shaped inputs — plus the math/statistics namespaces.
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ComputeValidationError("underscore attributes are not allowed")
            if node.attr in ("format", "format_map", "mro"):
                raise ComputeValidationError(
                    f".{node.attr} is not allowed (use f-strings for formatting)"
                )

        if isinstance(node, ast.Name):
            if node.id.startswith("__"):
                raise ComputeValidationError("dunder names are not allowed")

        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, int) and abs(v) > MAX_CONST_INT:
                raise ComputeValidationError(
                    f"integer literal too large (|{v}| > {MAX_CONST_INT})"
                )
            if isinstance(v, str) and len(v) > MAX_CONST_STR:
                raise ComputeValidationError("string literal too long")

        # Cap constant exponents; non-constant exponents are bounded by
        # the const-int cap + rlimits in the runner.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exp = node.right
            if isinstance(exp, ast.Constant) and isinstance(exp.value, (int, float)):
                if abs(exp.value) > 10_000:
                    raise ComputeValidationError("exponent too large")

    # Calls: bare-name calls must be whitelisted builtins or user-assigned
    # lambdas; module calls were already constrained by the Attribute rule.
    assigned: set[str] = {
        t.id
        for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        for t in stmt.targets
        if isinstance(t, ast.Name)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = node.func.id
            if fn not in _SAFE_BUILTIN_NAMES and fn not in assigned:
                raise ComputeValidationError(
                    f"call to {fn!r} is not allowed — allowed builtins: "
                    f"{', '.join(sorted(_SAFE_BUILTIN_NAMES))}, plus "
                    f"math.* / statistics.*"
                )
    return tree


# ── Runner (child process) ────────────────────────────────────────────
# Executed as `python -I -c RUNNER` with {"code": ...} on stdin. -I is
# isolated mode: no site-packages, no env hooks, no cwd on sys.path.
# rlimits make CPU/memory bombs die in the child, not the API worker.

_RUNNER = r"""
import ast, json, math, statistics, sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
    except Exception:
        pass  # RLIMIT_AS unreliable on macOS; CPU limit + timeout cover
except Exception:
    pass

def _brange(*a):
    r = range(*a)
    if len(r) > 200_000:
        raise ValueError("range too large (cap 200,000)")
    return r

_SAFE = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "len": len, "sorted": sorted, "reversed": reversed, "range": _brange,
    "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
    "any": any, "all": all, "int": int, "float": float, "str": str,
    "bool": bool, "list": list, "dict": dict, "set": set, "tuple": tuple,
    "divmod": divmod, "pow": pow,
}

def main() -> None:
    payload = json.loads(sys.stdin.read())
    code = payload["code"]
    tree = ast.parse(code, mode="exec")
    # Surface the trailing expression's value as the result.
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        tree.body[-1] = ast.Assign(
            targets=[ast.Name(id="_result_", ctx=ast.Store())],
            value=tree.body[-1].value,
        )
        ast.fix_missing_locations(tree)
    g = {"__builtins__": _SAFE, "math": math, "statistics": statistics}
    exec(compile(tree, "<compute>", "exec"), g)  # noqa: S102 — validated upstream
    result = g.get("_result_")

    def _default(o):
        if isinstance(o, (set, frozenset)):
            return sorted(o, key=str)
        return str(o)

    print(json.dumps({"ok": True, "result": result}, default=_default))

try:
    main()
except Exception as e:  # noqa: BLE001 — child reports, parent relays
    print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
"""


@dataclass
class ComputeResult:
    ok: bool
    result: Any = None
    error: str | None = None


def run_compute(code: str) -> ComputeResult:
    """Validate + execute `code` in the isolated runner. Never raises for
    code-level problems — returns ComputeResult(ok=False, error=...) so
    the tool loop can relay a self-correctable message to the model."""
    try:
        validate_code(code)
    except ComputeValidationError as e:
        return ComputeResult(ok=False, error=str(e))

    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _RUNNER],
            input=json.dumps({"code": code}),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return ComputeResult(ok=False, error=f"computation timed out ({TIMEOUT_S:.0f}s)")
    except Exception as e:  # noqa: BLE001 — spawn failure is an env problem
        logger.warning("compute runner spawn failed: %s", e)
        return ComputeResult(ok=False, error="compute runner unavailable")

    out = (proc.stdout or "").strip()
    if not out:
        err = (proc.stderr or "").strip()[:200]
        return ComputeResult(ok=False, error=f"compute produced no output ({err or 'killed'})")
    if len(out) > MAX_RESULT_CHARS:
        return ComputeResult(ok=False, error="result too large — reduce the output size")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return ComputeResult(ok=False, error="compute returned malformed output")
    if not payload.get("ok"):
        return ComputeResult(ok=False, error=str(payload.get("error") or "unknown error"))
    return ComputeResult(ok=True, result=payload.get("result"))
