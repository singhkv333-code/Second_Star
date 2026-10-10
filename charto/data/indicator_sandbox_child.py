"""The process an AI-written indicator runs in. Never imported by the server.

Started by indicator_sandbox.py as `python -I -S -B <this file>`: isolated
mode (no PYTHON* env, no user site, script dir not on sys.path) and no site
packages, with an EMPTY environment, in a scratch working directory, and —
when the server runs as root — as uid/gid 65534. Reads one JSON request on
stdin, writes one JSON reply on stdout, exits.

What stands between the generated code and the machine, outermost first. No
single layer is trusted to hold; an escape from one lands inside the next:

  1. a separate short-lived process (a crash or a hang costs one request)
  2. the parent's wall-clock timeout, and RLIMIT_CPU below it
  3. RLIMIT_AS / RLIMIT_DATA (memory), RLIMIT_FSIZE=0 (no file writes),
     RLIMIT_NOFILE, RLIMIT_NPROC=0 (no fork) where the platform enforces them
  4. an AST allowlist, checked by the parent AND again here: no imports, no
     names or attributes starting with "_", no class/global/nonlocal/with/
     async/yield, no string-format attribute paths
  5. restricted builtins: no open/eval/exec/compile/__import__/getattr/
     type/vars/globals/locals/input/print/breakpoint/memoryview
  6. an audit hook installed BEFORE the code is compiled that refuses file
     opens, sockets, subprocesses, os.* process calls, ctypes and any import
     of a module not already loaded. PEP 578 says plainly this is a tripwire,
     not a sandbox — it is here as the last layer, not the first.

A deployment that runs other people's code at scale should add a kernel-level
jail outside all of this (nsjail/bubblewrap with no network namespace, or a
microVM); indicator_sandbox.py takes it as CHARTO_SANDBOX_PREFIX.

The code is given a Pine-like `ta` namespace (ta.sma, ta.ema, ta.rma …). Its
moving averages ARE the native engine's functions, imported from
indicators.py, so a custom study built on ta.rma agrees with the native RSI
to the last digit instead of to a re-implementation's rounding.
"""
import json
import math
import sys
import time as _time
from types import SimpleNamespace

# ── resource limits, before anything untrusted is even parsed ─────────────
try:
    import resource

    def _cap(which, soft, hard=None):
        try:
            cur_soft, cur_hard = resource.getrlimit(which)
            hard = soft if hard is None else hard
            if cur_hard != resource.RLIM_INFINITY:
                hard = min(hard, cur_hard)
                soft = min(soft, hard)
            resource.setrlimit(which, (soft, hard))
        except (ValueError, OSError):
            pass  # not enforceable on this platform (macOS ignores RLIMIT_AS)

    _cpu = 20
    try:
        _cpu = max(1, min(120, int(sys.argv[1]))) if len(sys.argv) > 1 else 20
    except ValueError:
        pass
    _cap(resource.RLIMIT_CPU, _cpu, _cpu + 1)
    _cap(resource.RLIMIT_FSIZE, 0)
    _cap(resource.RLIMIT_NOFILE, 16)
    if sys.platform.startswith("linux"):
        _cap(resource.RLIMIT_AS, 768 * 1024 * 1024)
        _cap(resource.RLIMIT_NPROC, 0)
    _cap(resource.RLIMIT_DATA, 768 * 1024 * 1024)
    _cap(resource.RLIMIT_CORE, 0)
except ImportError:  # pragma: no cover — non-POSIX
    pass

import ast  # noqa: E402

# The native engine — trusted, ours, stdlib-only. Loaded from an explicit path
# because -I keeps this file's directory off sys.path.
_HERE = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
sys.path.insert(0, _HERE)
import indicators as _native  # noqa: E402
sys.path.pop(0)

# ── the policy (the parent imports the same constants from this file) ────
ALLOWED_NODES = {
    "Module", "FunctionDef", "arguments", "arg", "Return", "Assign",
    "AugAssign", "AnnAssign", "For", "While", "If", "Break", "Continue",
    "Pass", "Expr", "BoolOp", "BinOp", "UnaryOp", "Compare", "IfExp", "Call",
    "keyword", "Name", "Load", "Store", "Del", "Delete", "Constant", "List",
    "Tuple", "Dict", "Set", "ListComp", "DictComp", "SetComp", "GeneratorExp",
    "comprehension", "Subscript", "Slice", "Attribute", "Starred", "Lambda",
    "Assert", "Raise", "Try", "ExceptHandler", "JoinedStr", "FormattedValue",
    "And", "Or", "Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow",
    "USub", "UAdd", "Not", "Invert", "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE",
    "Is", "IsNot", "In", "NotIn", "BitAnd", "BitOr", "BitXor", "LShift",
    "RShift", "MatMult",
}
# str.format / format_map resolve "{0.__class__}" inside a string, where no
# AST check can see the attribute path.
BANNED_ATTRS = {"format", "format_map", "mro", "gi_frame", "f_globals",
                "f_locals", "f_back", "cr_frame", "ag_frame", "tb_frame"}
MAX_CODE_CHARS = 30000


def check_source(code: str) -> list[str]:
    """Every policy violation in `code`, as human-readable lines. Empty = ok."""
    if not isinstance(code, str) or not code.strip():
        return ["no code"]
    if len(code) > MAX_CODE_CHARS:
        return [f"code is {len(code)} characters; the limit is {MAX_CODE_CHARS}"]
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return [f"syntax error line {exc.lineno}: {exc.msg}"]
    problems = []
    for node in ast.walk(tree):
        kind = type(node).__name__
        line = getattr(node, "lineno", "?")
        if kind not in ALLOWED_NODES:
            problems.append(f"line {line}: {kind} is not allowed "
                            f"(no imports, classes, with, async, yield, global)")
            continue
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            problems.append(f"line {line}: names starting with '_' are not allowed ({node.id})")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in BANNED_ATTRS:
                problems.append(f"line {line}: attribute '.{node.attr}' is not allowed")
        if isinstance(node, ast.arg) and node.arg.startswith("_"):
            problems.append(f"line {line}: parameter names starting with '_' are not allowed")
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            problems.append(f"line {line}: decorators are not allowed")
    top = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "compute"]
    if not top:
        problems.append("the code must define a top-level function compute(bars, params)")
    elif len(top[0].args.args) != 2:
        problems.append("compute must take exactly two arguments: (bars, params)")
    for n in tree.body:
        if not isinstance(n, (ast.FunctionDef, ast.Assign, ast.AnnAssign, ast.Expr)):
            problems.append(f"line {n.lineno}: only functions and constants belong at the top level")
        elif isinstance(n, ast.Expr) and not (isinstance(n.value, ast.Constant)
                                              and isinstance(n.value.value, str)):
            problems.append(f"line {n.lineno}: top-level statements other than definitions are not allowed")
    return problems


# ── the `ta` namespace ────────────────────────────────────────────────────
def _tail(v):
    """(start, values) of the last run without None — moving averages over a
    series with a warmup (an SMA of an RSI) start where it does."""
    start = 0
    for i, x in enumerate(v):
        if x is None:
            start = i + 1
    return start, [float(x) for x in v[start:]]


def _wrap(fn):
    def run(src, length):
        n = int(length)
        if n < 1:
            raise ValueError("length must be at least 1")
        start, vals = _tail(list(src))
        if len(vals) < n:
            return [None] * len(src)
        return [None] * start + fn(vals, n)
    return run


def _rolling(src, length, fn):
    n = int(length)
    if n < 1:
        raise ValueError("length must be at least 1")
    out = []
    for i in range(len(src)):
        if i < n - 1:
            out.append(None)
            continue
        w = src[i - n + 1:i + 1]
        out.append(None if any(x is None for x in w) else fn(w))
    return out


class _TA:
    """Pine's ta.* vocabulary over Python lists. Every function is causal: the
    value at bar i reads bars 0..i only. There is deliberately no way to shift
    a series forward."""

    sma = staticmethod(_wrap(_native.sma))
    ema = staticmethod(_wrap(_native.ema))
    rma = staticmethod(_wrap(_native.wilder))
    wma = staticmethod(_wrap(_native.wma))
    hma = staticmethod(_wrap(_native.hma))
    stdev = staticmethod(_wrap(_native.stdev))

    @staticmethod
    def vwma(src, volume, length):
        n = int(length)
        start, _ = _tail(list(src))
        s = [float(x) for x in src[start:]]
        v = [float(x) for x in volume[start:]]
        if len(s) < n:
            return [None] * len(src)
        return [None] * start + _native.vwma(s, v, n)

    @staticmethod
    def dema(src, length):
        e1 = _TA.ema(src, length)
        e2 = _TA.ema(e1, length)
        return [None if a is None or b is None else 2 * a - b for a, b in zip(e1, e2)]

    @staticmethod
    def tema(src, length):
        e1 = _TA.ema(src, length)
        e2 = _TA.ema(e1, length)
        e3 = _TA.ema(e2, length)
        return [None if a is None or b is None or c is None else 3 * a - 3 * b + c
                for a, b, c in zip(e1, e2, e3)]

    @staticmethod
    def variance(src, length):
        sd = _TA.stdev(src, length)
        return [None if x is None else x * x for x in sd]

    @staticmethod
    def highest(src, length):
        return _rolling(src, length, max)

    @staticmethod
    def lowest(src, length):
        return _rolling(src, length, min)

    @staticmethod
    def sum(src, length):
        return _rolling(src, length, math.fsum)

    @staticmethod
    def highestbars(src, length):
        """Bars since the highest value of the last `length` (0 = this bar)."""
        def f(w):
            m = max(w)
            return len(w) - 1 - max(i for i, x in enumerate(w) if x == m)
        return _rolling(src, length, f)

    @staticmethod
    def lowestbars(src, length):
        def f(w):
            m = min(w)
            return len(w) - 1 - max(i for i, x in enumerate(w) if x == m)
        return _rolling(src, length, f)

    @staticmethod
    def median(src, length):
        def f(w):
            s = sorted(w)
            k = len(s) // 2
            return s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2
        return _rolling(src, length, f)

    @staticmethod
    def percentrank(src, length):
        """Percent of the previous `length` values <= the current one (Pine)."""
        n = int(length)
        out = []
        for i in range(len(src)):
            if i < n or src[i] is None or any(x is None for x in src[i - n:i]):
                out.append(None)
                continue
            out.append(100.0 * sum(1 for x in src[i - n:i] if x <= src[i]) / n)
        return out

    @staticmethod
    def linreg(src, length, offset=0):
        """Least-squares line over the last `length` values, read at
        length-1-offset (Pine's ta.linreg)."""
        n = int(length)
        sx = n * (n - 1) / 2
        sxx = (n - 1) * n * (2 * n - 1) / 6
        den = n * sxx - sx * sx

        def f(w):
            sy = math.fsum(w)
            sxy = math.fsum(j * x for j, x in enumerate(w))
            slope = (n * sxy - sx * sy) / den if den else 0.0
            return (sy - slope * sx) / n + slope * (n - 1 - offset)
        return _rolling(src, n, f)

    @staticmethod
    def change(src, length=1):
        n = int(length)
        if n < 0:
            raise ValueError("change() looks back; length must be >= 0")
        return [None if i < n or src[i] is None or src[i - n] is None
                else src[i] - src[i - n] for i in range(len(src))]

    @staticmethod
    def mom(src, length):
        return _TA.change(src, length)

    @staticmethod
    def roc(src, length):
        n = int(length)
        return [None if i < n or src[i] is None or not src[i - n]
                else 100.0 * (src[i] - src[i - n]) / src[i - n] for i in range(len(src))]

    @staticmethod
    def shift(src, bars=1):
        """The value `bars` bars AGO. Negative values would read the future
        and are refused."""
        n = int(bars)
        if n < 0:
            raise ValueError("shift() only looks back; a negative shift reads future bars")
        return [None] * min(n, len(src)) + list(src[:max(0, len(src) - n)])

    @staticmethod
    def tr(high, low, close):
        out = []
        for i in range(len(close)):
            h, l = high[i], low[i]
            if i == 0:
                out.append(h - l)
            else:
                pc = close[i - 1]
                out.append(max(h - l, abs(h - pc), abs(l - pc)))
        return out

    @staticmethod
    def atr(high, low, close, length):
        return _TA.rma(_TA.tr(high, low, close), length)

    @staticmethod
    def rsi(src, length):
        n = int(length)
        up = [None] + [max(src[i] - src[i - 1], 0.0) for i in range(1, len(src))]
        dn = [None] + [max(src[i - 1] - src[i], 0.0) for i in range(1, len(src))]
        au, ad = _TA.rma(up, n), _TA.rma(dn, n)
        return [None if a is None or b is None else (100.0 if b == 0 else 100 - 100 / (1 + a / b))
                for a, b in zip(au, ad)]

    @staticmethod
    def cum(src):
        out, s = [], 0.0
        for x in src:
            s += 0.0 if x is None else x
            out.append(s)
        return out

    @staticmethod
    def crossover(a, b):
        return [i > 0 and None not in (a[i], b[i], a[i - 1], b[i - 1])
                and a[i] > b[i] and a[i - 1] <= b[i - 1] for i in range(len(a))]

    @staticmethod
    def crossunder(a, b):
        return [i > 0 and None not in (a[i], b[i], a[i - 1], b[i - 1])
                and a[i] < b[i] and a[i - 1] >= b[i - 1] for i in range(len(a))]

    @staticmethod
    def nz(src, replacement=0.0):
        if isinstance(src, (list, tuple)):
            return [replacement if x is None else x for x in src]
        return replacement if src is None else src

    @staticmethod
    def valuewhen(condition, src, occurrence=0):
        """src at the `occurrence`-th most recent bar where condition held."""
        k = int(occurrence)
        hits, out = [], []
        for i in range(len(src)):
            if condition[i]:
                hits.append(src[i])
            out.append(hits[-1 - k] if len(hits) > k else None)
        return out

    @staticmethod
    def barssince(condition):
        out, last = [], None
        for i, c in enumerate(condition):
            if c:
                last = i
            out.append(None if last is None else i - last)
        return out

    # ── across a basket: one value per bar from many members' series ──────
    # Each takes a list of member series (bars["basket"]["close"], or series
    # computed from them) and reads bar i of every member — never another
    # bar — so a cross-sectional study is as causal as its inputs. Members
    # with no value at a bar (not yet listed, a gap) are left out of that
    # bar; a bar where no member has a value is None.

    @staticmethod
    def xmean(series_list):
        n = len(series_list[0]) if series_list else 0
        out = []
        for i in range(n):
            v = [s[i] for s in series_list if s[i] is not None]
            out.append(sum(v) / len(v) if v else None)
        return out

    @staticmethod
    def xmedian(series_list):
        n = len(series_list[0]) if series_list else 0
        out = []
        for i in range(n):
            v = sorted(s[i] for s in series_list if s[i] is not None)
            k = len(v)
            out.append(None if not k else v[k // 2] if k % 2 else (v[k // 2 - 1] + v[k // 2]) / 2)
        return out

    @staticmethod
    def xsum(series_list):
        n = len(series_list[0]) if series_list else 0
        out = []
        for i in range(n):
            v = [s[i] for s in series_list if s[i] is not None]
            out.append(sum(v) if v else None)
        return out

    @staticmethod
    def xcount(cond_list):
        """How many members' condition holds at each bar (None = no value)."""
        n = len(cond_list[0]) if cond_list else 0
        out = []
        for i in range(n):
            v = [c[i] for c in cond_list if c[i] is not None]
            out.append(float(sum(1 for x in v if x)) if v else None)
        return out

    @staticmethod
    def xpct(cond_list):
        """Percent (0-100) of the members WITH A VALUE whose condition holds."""
        n = len(cond_list[0]) if cond_list else 0
        out = []
        for i in range(n):
            v = [c[i] for c in cond_list if c[i] is not None]
            out.append(100.0 * sum(1 for x in v if x) / len(v) if v else None)
        return out

    @staticmethod
    def xrank(src, series_list):
        """Percentile (0-100) of src[i] among the members' values at bar i."""
        out = []
        for i in range(len(src)):
            v = [s[i] for s in series_list if s[i] is not None]
            if src[i] is None or not v:
                out.append(None)
                continue
            out.append(100.0 * sum(1 for x in v if x <= src[i]) / len(v))
        return out


TA = _TA()

_BASKET_COLS = ("open", "high", "low", "close", "volume")


def _basket(b, cut=None):
    """A fresh copy of a basket, optionally cut to the first `cut` bars —
    the look-ahead check truncates the members exactly as it truncates the
    chart's own bars."""
    if not isinstance(b, dict):
        return b
    out = {"label": b.get("label", ""), "symbols": list(b.get("symbols") or [])}
    for k in _BASKET_COLS:
        out[k] = [list(s[:cut] if cut else s) for s in (b.get(k) or [])]
    return out

SAFE_BUILTINS = {
    k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
    for k in ("abs", "all", "any", "bool", "dict", "divmod", "enumerate",
              "filter", "float", "int", "isinstance", "len", "list", "map",
              "max", "min", "pow", "range", "reversed", "round", "set",
              "slice", "sorted", "str", "sum", "tuple", "zip", "True", "False",
              "None", "ValueError", "ZeroDivisionError", "ArithmeticError",
              "IndexError", "KeyError", "TypeError", "OverflowError", "Exception")
}
_MATH = SimpleNamespace(**{k: getattr(math, k) for k in dir(math) if not k.startswith("_")})


def _run(code: str, jobs: list, datasets: dict | None = None) -> dict:
    problems = check_source(code)
    if problems:
        return {"ok": False, "stage": "policy", "error": "; ".join(problems[:8])}

    # The tripwire goes in BEFORE compile/exec: from here on, nothing may open
    # a file, a socket or a process, or import a module that is not already
    # loaded. Our own reply goes out over sys.stdout, which is already open.
    loaded = set(sys.modules)
    deny_prefix = ("socket.", "subprocess.", "os.system", "os.exec", "os.fork",
                   "os.posix_spawn", "os.spawn", "os.kill", "os.remove",
                   "os.rename", "os.mkdir", "os.rmdir", "os.unlink",
                   "os.chmod", "os.chown", "os.truncate", "ctypes.", "shutil.",
                   "urllib.", "http.", "ftplib.", "smtplib.", "webbrowser.",
                   "sys._getframe", "sys.settrace", "sys.setprofile",
                   "object.__setattr__", "object.__delattr__", "code.",
                   "pty.", "resource.setrlimit", "winreg.", "msvcrt.")

    def hook(event, args):
        if event == "open":
            raise PermissionError("file access is not available to indicators")
        if event == "import" and args and args[0] not in loaded:
            raise PermissionError(f"importing {args[0]} is not available to indicators")
        if event.startswith(deny_prefix):
            raise PermissionError(f"{event} is not available to indicators")

    sys.addaudithook(hook)

    env = {"__builtins__": SAFE_BUILTINS, "ta": TA, "math": _MATH,
           "nan": float("nan"), "inf": float("inf")}
    try:
        exec(compile(code, "<indicator>", "exec"), env)  # noqa: S102 — the point of this file
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "load", "error": f"{type(exc).__name__}: {exc}"}
    compute = env.get("compute")
    if not callable(compute):
        return {"ok": False, "stage": "load", "error": "compute is not a function"}

    results = []
    for job in jobs:
        # a job carries its bars, or names a shared dataset — the validator
        # runs ~40 jobs over a handful of datasets and sends each once
        cols = job.get("bars") or (datasets or {})[job["data"]]
        cut = job.get("truncate")
        if cut:
            cols = {k: (v[:cut] if isinstance(v, list) else
                        _basket(v, cut) if k == "basket" else v) for k, v in cols.items()}
        n = len(cols["close"])
        bars = dict(cols)
        bars["n"] = n
        o, h, l, c = cols["open"], cols["high"], cols["low"], cols["close"]
        bars["hl2"] = [(h[i] + l[i]) / 2 for i in range(n)]
        bars["hlc3"] = [(h[i] + l[i] + c[i]) / 3 for i in range(n)]
        bars["ohlc4"] = [(o[i] + h[i] + l[i] + c[i]) / 4 for i in range(n)]
        outs = []
        t0 = _time.perf_counter()
        try:
            for _ in range(int(job.get("repeat") or 1)):
                # a fresh copy each run: code that mutates its inputs must not
                # change what the next run (or the determinism check) sees
                got = compute({k: (list(v) if isinstance(v, list) else
                                   _basket(v) if k == "basket" else v)
                               for k, v in bars.items()}, dict(job.get("params") or {}))
                outs.append(_normalise(got, n))
        except Exception as exc:  # noqa: BLE001
            results.append({"id": job.get("id"), "ok": False,
                            "error": f"{type(exc).__name__}: {exc}",
                            "line": _err_line(exc)})
            continue
        ms = (_time.perf_counter() - t0) * 1000 / max(1, len(outs))
        bad = next((o for o in outs if isinstance(o, str)), None)
        if bad:
            results.append({"id": job.get("id"), "ok": False, "error": bad})
            continue
        res = {"id": job.get("id"), "ok": True, "lines": outs[0], "ms": round(ms, 2)}
        if len(outs) > 1:
            res["deterministic"] = all(o == outs[0] for o in outs[1:])
        results.append(res)
    return {"ok": True, "results": results}


def _err_line(exc):
    tb = exc.__traceback__
    line = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<indicator>":
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def _normalise(got, n):
    """{line: [float|None]*n} or an error string. NaN and inf are passed on
    as the strings "nan"/"inf" so the validator can count them — JSON has no
    spelling for either, and silently mapping them to null would hide them."""
    if not isinstance(got, dict) or not got:
        return f"compute must return a non-empty dict of line -> list, got {type(got).__name__}"
    out = {}
    for k, v in got.items():
        if not isinstance(k, str):
            return "line names must be strings"
        if not isinstance(v, (list, tuple)):
            return f"line '{k}' is {type(v).__name__}, expected a list"
        if len(v) != n:
            return f"line '{k}' has {len(v)} values for {n} bars"
        col = []
        for x in v:
            if x is None:
                col.append(None)
            elif isinstance(x, bool):
                col.append(1.0 if x else 0.0)
            elif isinstance(x, (int, float)):
                fx = float(x)
                col.append(fx if math.isfinite(fx) else ("nan" if fx != fx else "inf"))
            else:
                return f"line '{k}' contains {type(x).__name__}; values must be numbers or None"
        out[k] = col
    return out


def main():
    try:
        req = json.loads(sys.stdin.read())
        reply = _run(req.get("code", ""), req.get("jobs") or [], req.get("datasets"))
    except Exception as exc:  # noqa: BLE001
        reply = {"ok": False, "stage": "sandbox", "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(reply, separators=(",", ":")))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
