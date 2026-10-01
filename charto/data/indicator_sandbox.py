"""Run AI-written indicator code — only ever in a child process.

The server never imports, evals or execs generated code. Every run, whether
the validator is testing a draft or the chart is fetching a validated study,
goes through `run()`, which starts indicator_sandbox_child.py in a fresh,
isolated interpreter and reads one JSON reply back. The layers that child
applies are listed in its docstring; this side adds the ones only a parent
can: the wall-clock timeout, the empty environment, a scratch cwd, dropping
to uid 65534 when the server runs as root, and an optional kernel jail
prefix (CHARTO_SANDBOX_PREFIX, e.g. an nsjail or bwrap command line).

Measured: interpreter start-up with -I -S is ~20 ms, so a chart fetch of a
custom study costs one start-up plus the study's own arithmetic. Results are
cached per (code version, bars, params) so a chart that redraws does not
re-run anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

CHILD = Path(__file__).with_name("indicator_sandbox_child.py")

# The policy check is defined once, in the child, and imported here so the
# parent can refuse a draft before paying for a process. Importing the child
# module runs only its definitions (main() is guarded) — but it also applies
# the resource caps at import time, which must never happen to the SERVER. So
# the check is loaded from source with the cap block skipped.
_policy_ns: dict = {}


def _load_policy() -> None:
    src = CHILD.read_text(encoding="utf-8")
    start = src.index("# ── the policy")
    end = src.index("# ── the `ta` namespace")
    exec(compile("import ast\n" + src[start:end], str(CHILD), "exec"), _policy_ns)  # noqa: S102 — our own file


_load_policy()
check_source = _policy_ns["check_source"]
MAX_CODE_CHARS = _policy_ns["MAX_CODE_CHARS"]

PREFIX = shlex.split(os.environ.get("CHARTO_SANDBOX_PREFIX", ""))
MAX_PARALLEL = max(1, int(os.environ.get("CHARTO_SANDBOX_PARALLEL", "4") or 4))
_slots = threading.BoundedSemaphore(MAX_PARALLEL)
_scratch = Path(tempfile.gettempdir()) / "charto-sandbox"


class SandboxError(RuntimeError):
    """The child could not run the code at all (timeout, crash, policy)."""


def _argv(cpu_s: int) -> list[str]:
    return [*PREFIX, sys.executable, "-I", "-S", "-B", str(CHILD), str(cpu_s)]


def run(code: str, jobs: list[dict], *, timeout: float = 15.0,
        datasets: dict | None = None) -> dict:
    """Run `code` against each job in one child. Returns the child's reply:
    {"ok": True, "results": [...]} or {"ok": False, "stage", "error"}.

    Raises SandboxError only for failures of the sandbox itself — a timeout,
    a killed child, unreadable output — which the validator reports as a
    failed check rather than a crash.
    """
    problems = check_source(code)
    if problems:
        return {"ok": False, "stage": "policy", "error": "; ".join(problems[:8])}
    payload = json.dumps({"code": code, "jobs": jobs, "datasets": datasets or {}},
                         separators=(",", ":"))
    kw: dict = {}
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # never run someone else's code as root
        kw.update(user=65534, group=65534, extra_groups=[])
    _scratch.mkdir(mode=0o777, exist_ok=True)
    cpu = max(2, int(timeout))
    if not _slots.acquire(timeout=timeout):
        raise SandboxError("the indicator sandbox is busy — retry shortly")
    try:
        proc = subprocess.run(
            _argv(cpu), input=payload.encode(), capture_output=True,
            timeout=timeout, env={}, cwd=str(_scratch), check=False, **kw)
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"timed out after {timeout:.0f}s") from exc
    finally:
        _slots.release()
    out = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0 and not out:
        sig = -proc.returncode if proc.returncode < 0 else None
        why = ("CPU limit exceeded" if sig == 24 else
               "killed (memory or CPU limit)" if sig in (9, 6) else
               f"exited {proc.returncode}")
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise SandboxError(f"{why} {err[0]}".strip())
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise SandboxError(f"unreadable reply: {out[:200]}") from exc


# ── the chart path: one study, one series, cached ────────────────────────
_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 256


def columns(rows: list[tuple], *, interval: str = "", tz_offset: int = 0) -> dict:
    """Charto rows (t, o, h, l, c, v) → the column dict compute() receives."""
    return {"time": [r[0] for r in rows], "open": [r[1] for r in rows],
            "high": [r[2] for r in rows], "low": [r[3] for r in rows],
            "close": [r[4] for r in rows], "volume": [r[5] or 0 for r in rows],
            "interval": interval, "tz_offset": int(tz_offset or 0)}


def compute(code: str, rows: list[tuple], params: dict, *, interval: str = "",
            tz_offset: int = 0, timeout: float = 10.0) -> tuple[dict, int]:
    """({line: [float|None]}, non-finite count) for one validated study. Raises
    ValueError with the child's own message when the code fails here — a
    study that passed validation can still meet bars it never saw."""
    key = hashlib.sha256(json.dumps(
        [code, len(rows), rows[0] if rows else None, rows[-1] if rows else None,
         sorted((params or {}).items()), interval, tz_offset],
        default=str).encode()).hexdigest()
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    try:
        reply = run(code, [{"id": "chart", "bars": columns(rows, interval=interval,
                                                           tz_offset=tz_offset),
                            "params": params or {}}], timeout=timeout)
    except SandboxError as exc:
        raise ValueError(f"custom indicator could not run: {exc}") from exc
    if not reply.get("ok"):
        raise ValueError(f"custom indicator could not load: {reply.get('error')}")
    res = reply["results"][0]
    if not res.get("ok"):
        raise ValueError(f"custom indicator failed on these bars: {res.get('error')}")
    # NaN/inf on bars the validator never saw are drawn as gaps, and COUNTED
    # so the caller can say so — a gap must never pass for a value
    bad = sum(1 for v in res["lines"].values() for x in v if isinstance(x, str))
    lines = {k: [None if isinstance(x, str) else x for x in v]
             for k, v in res["lines"].items()}
    out = (lines, bad)
    with _cache_lock:
        _cache[key] = out
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return out
