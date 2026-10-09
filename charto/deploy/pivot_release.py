#!/usr/bin/env python3
"""Deploy the `production-live` branch onto the production VM.

    pivot_release.py              one poll: deploy if the branch moved (the timer)
    pivot_release.py --plan       print what the next deploy would do, change nothing
    pivot_release.py --retry      forget the last failure and try that commit again
    pivot_release.py --status     print the state file

THE CONTRACT
------------
`production-live` on GitHub IS what this box runs. Moving the branch deploys;
moving it back rolls back (the diff runs between whatever is deployed and
whatever the branch names, in either direction). Nothing else changes the
release: a hand-copied file is overwritten the next time a commit touches it.

The release (/etc/pivot/stage-release) is a TRIMMED tree — no docs, tests,
charto/browser, charto/media, pivot/scripts — so this never copies the whole
branch in. It applies exactly the paths that changed between the deployed
commit and the new one, and of those only the ones the release carries.

ORDER, chosen so a failure leaves the site as it was
----------------------------------------------------
  1. fetch the branch into a bare partial mirror (as pivot-build; anonymous —
     the repository is public, so no credential lives on this box)
  2. plan: diff deployed..new, keep what the release carries
  3. if a Next app changed, BUILD it beside the release first (as pivot-build).
     A failed build stops here; nothing on disk has moved.
  4. install the files (backups under STATE/backups/<sha>, not beside them),
     py_compile / node --check what changed, restart the services whose code
     moved, health-check each one
  5. swap each new .next in, restart, health-check
  Any failure after 4 starts unwinds everything done so far, in reverse, and
  records the commit as failed so the timer does not rebuild it every minute.
  A new commit on the branch is tried normally; `--retry` re-tries the same one.

Only file copies run as root. Everything that executes repository code — git,
`pnpm install --ignore-scripts`, `next build` — runs as pivot-build, and the
services run as their own users.
"""
from __future__ import annotations

import fcntl
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_URL = os.environ.get("PIVOT_RELEASE_REPO", "https://github.com/singhkv333-code/Second_Star.git")
BRANCH = os.environ.get("PIVOT_RELEASE_BRANCH", "production-live")
STATE = Path(os.environ.get("PIVOT_RELEASE_STATE", "/var/lib/pivot-release"))
MIRROR = Path(os.environ.get("PIVOT_RELEASE_MIRROR", "/opt/pivot/repo.git"))
POINTER = Path(os.environ.get("PIVOT_RELEASE_POINTER", "/etc/pivot/stage-release"))
BUILD_ROOT = Path(os.environ.get("PIVOT_RELEASE_BUILD_ROOT", "/opt/pivot"))
BUILD_USER = os.environ.get("PIVOT_RELEASE_BUILD_USER", "pivot-build")   # "" = run as self (tests)
PYTHON = os.environ.get("PIVOT_RELEASE_PYTHON", "/opt/pivot/venv/bin/python")
HEALTH_WAIT = float(os.environ.get("PIVOT_RELEASE_HEALTH_WAIT", "180"))

TOPS = ("charto/", "pivot/", "pivot-next/", "pivotted/")
# Trees the release was cut without. A new file under one of these stays out.
TRIMMED = ("charto/browser/", "charto/media/", "pivot/archive/", "pivot/infra/",
           "pivot/migrations/", "pivot/scripts/", "pivot/tests/")
# Tracked but deliberately not shipped: it would switch Google sign-in on.
NEVER = ("charto/web/.env.local",)

# A service restarts when code it imports moved. charto imports pivot/ through
# execution_bridge, so pivot/ restarts both.
SERVICES = [
    ("charto", ("charto/data/", "pivot/"), "http://127.0.0.1:5174/health"),
    ("pivot-api", ("pivot/",), "http://127.0.0.1:8000/health"),
]
NEXT_APPS = [  # (source dir, unit, port)
    ("pivot-next", "pivot-next", 3000),
    ("charto/web", "charto-web", 5175),
]
NEXT_ENV = {
    "NEXT_TELEMETRY_DISABLED": "1",
    "PIVOT_BACKEND_ORIGIN": "http://127.0.0.1:8000",
    "CHARTO_BACKEND": "http://127.0.0.1:5174",
    "NEXT_PUBLIC_PIVOT_API_BASE": "/pv/api",
    "NEXT_PUBLIC_PIVOT_WS_BASE": "wss://pivot-india.centralindia.cloudapp.azure.com/pv/api",
    "NODE_OPTIONS": "--max-old-space-size=3072",
}
DEPENDENCY_FILES = ("pivot/requirements.txt", "charto/data/requirements.txt")


class Failed(Exception):
    """A deploy step failed; the message is what the state file records."""


def say(*a) -> None:
    print(*a, flush=True)


def run(cmd: list[str], *, as_build: bool = False, cwd: Path | None = None,
        env: dict | None = None, check: bool = True, timeout: float | None = None,
        text: bool = True) -> subprocess.CompletedProcess:
    if as_build and BUILD_USER:
        # setpriv, not runuser: runuser opens a PAM session and logs two lines
        # per call, which every idle poll would write to the journal.
        home = pwd.getpwnam(BUILD_USER).pw_dir
        cmd = ["setpriv", f"--reuid={BUILD_USER}", f"--regid={BUILD_USER}", "--init-groups",
               "env", f"HOME={home}", f"USER={BUILD_USER}",
               *[f"{k}={v}" for k, v in (env or {}).items()], *cmd]
        env = None
    full_env = {**os.environ, **env} if env else None
    r = subprocess.run(cmd, cwd=cwd, env=full_env, capture_output=True, text=text, timeout=timeout)
    if check and r.returncode:
        err = r.stderr if text else r.stderr.decode(errors="replace")
        raise Failed(f"{' '.join(map(str, cmd[-4:]))}: {err.strip()[-600:]}")
    return r


def git(*args: str, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    return run(["git", "--git-dir", str(MIRROR), *args], as_build=True, check=check, text=text)


# ── state ────────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        return json.loads((STATE / "state.json").read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state: dict) -> None:
    tmp = STATE / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, STATE / "state.json")


def release() -> Path:
    return Path(POINTER.read_text().strip())


# ── fetch + plan ─────────────────────────────────────────────────────────

def ensure_mirror() -> None:
    if (MIRROR / "HEAD").exists():
        return
    MIRROR.mkdir(parents=True, exist_ok=True)
    if BUILD_USER:
        shutil.chown(MIRROR, BUILD_USER, BUILD_USER)
    git("init", "--quiet", "--bare")
    git("config", "remote.origin.url", REPO_URL)
    git("config", "remote.origin.promisor", "true")
    git("config", "remote.origin.partialclonefilter", "blob:none")


def fetch() -> str | None:
    """The commit the branch names on GitHub, or None if it does not exist."""
    ensure_mirror()
    r = git("fetch", "--quiet", "--no-tags", "--filter=blob:none", "origin",
            f"+refs/heads/{BRANCH}:refs/heads/{BRANCH}", check=False)
    if r.returncode:
        if "couldn't find remote ref" in r.stderr:
            return None
        raise Failed("fetch: " + r.stderr.strip()[-400:])
    return git("rev-parse", f"refs/heads/{BRANCH}").stdout.strip()


def _carried(path: str, status: str, R: Path) -> str | None:
    """Why this changed path is NOT applied, or None when it is."""
    if not path.startswith(TOPS):
        return "outside the release"
    if path in NEVER or Path(path).name.startswith(".env"):
        return "never shipped"
    if path.startswith(TRIMMED):
        return "trimmed tree"
    exists = (R / path).is_file()
    if status == "D":
        return None if exists else "not in release"
    if status == "M" and not exists:
        return "not in release"          # the release was cut without it
    if status == "A" and path.count("/") == 1 and path.split("/")[0] in ("charto", "pivot"):
        return "top-level doc/config"    # charto/README.md, pivot/Makefile …
    return None


def plan(base: str, new: str, R: Path) -> tuple[list[tuple[str, str, int]], list[tuple[str, str]]]:
    """([(op, path, mode)], [(path, reason skipped)]) for base → new."""
    out = git("diff", "--name-status", "--no-renames", "-z", base, new, "--",
              *[t.rstrip("/") for t in TOPS]).stdout.split("\0")
    pairs = [(out[i][0], out[i + 1]) for i in range(0, len(out) - 1, 2) if out[i]]
    modes = {}
    if pairs:
        for line in git("ls-tree", "-r", "-z", new, "--", *[p for s, p in pairs if s != "D"]).stdout.split("\0"):
            if line:
                meta, path = line.split("\t", 1)
                modes[path] = 0o755 if meta.split()[0] == "100755" else 0o644
    actions, skipped = [], []
    for status, path in pairs:
        status = "M" if status == "T" else status
        why = _carried(path, status, R)
        if why:
            skipped.append((path, why))
        elif status == "D":
            actions.append(("delete", path, 0))
        elif path in modes:                  # symlinks/submodules have no blob mode here
            actions.append(("install", path, modes[path]))
        else:
            skipped.append((path, "not a regular file"))
    return actions, skipped


def blob(commit: str, path: str) -> bytes:
    return git("show", f"{commit}:{path}", text=False).stdout


# ── steps ────────────────────────────────────────────────────────────────

def healthy(url: str, wait: float = HEALTH_WAIT, ok=lambda c: c == 200) -> bool:
    end = time.time() + wait
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if ok(r.status):
                    return True
        except urllib.error.HTTPError as e:
            if ok(e.code):
                return True
        except Exception:                    # noqa: BLE001 — not up yet
            pass
        if time.time() >= end:
            return False
        time.sleep(3)


def systemctl(*a: str) -> None:
    run(["systemctl", *a], timeout=120)


def build_next(app: str, new: str, actions, R: Path, tag: str) -> Path:
    """Build `app` at `new` beside the release. Returns the build dir."""
    B = BUILD_ROOT / f"build-{tag}" / app
    if B.exists():
        shutil.rmtree(B)
    B.mkdir(parents=True)
    # The release copy carries node_modules; .next is what we are replacing.
    tar = subprocess.Popen(["tar", "-C", str(R / app), "--exclude=./.next", "--exclude=./.next.*", "-cf", "-", "."],
                           stdout=subprocess.PIPE)
    subprocess.run(["tar", "-C", str(B), "-xf", "-"], stdin=tar.stdout, check=True)
    if tar.wait():
        raise Failed(f"copy {app} for build")
    prefix = app + "/"
    deps = False
    for op, path, mode in actions:
        if not path.startswith(prefix):
            continue
        dst = B / path[len(prefix):]
        if op == "delete":
            dst.unlink(missing_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(blob(new, path))
        os.chmod(dst, mode)
        deps |= dst.name in ("package.json", "pnpm-lock.yaml")
    if BUILD_USER:
        run(["chown", "-R", f"{BUILD_USER}:{BUILD_USER}", str(B.parent.parent)])
    if deps:
        say(f"  {app}: dependencies changed, installing")
        run(["/usr/local/bin/pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
            as_build=True, cwd=B, env={"NEXT_TELEMETRY_DISABLED": "1"}, timeout=1200)
    say(f"  {app}: next build")
    run(["nice", "-n", "10", "/usr/local/bin/pnpm", "exec", "next", "build"],
        as_build=True, cwd=B, env=NEXT_ENV, timeout=2400)
    std = B / ".next" / "standalone"
    if not (std / "server.js").is_file():
        raise Failed(f"{app}: build produced no standalone server")
    shutil.copytree(B / ".next" / "static", std / ".next" / "static", dirs_exist_ok=True)
    if (B / "public").is_dir():
        shutil.copytree(B / "public", std / "public", dirs_exist_ok=True)
    run(["chown", "-R", "root:root", str(B / ".next")])
    run(["chmod", "-R", "a+rX,go-w", str(B / ".next")])
    return B


def deploy(base: str, new: str, state: dict) -> dict:
    R = release()
    tag = new[:12]
    # Callables, run in REVERSE on failure. The first one runs last: restart
    # whatever was restarted, once every file is back the way it was.
    restarted: list[str] = []
    undo: list = [lambda: [systemctl("restart", u) for u in restarted]]
    bk = STATE / "backups" / tag
    builds = []
    try:
        actions, skipped = plan(base, new, R)
        paths = [p for _, p, _ in actions]
        say(f"deploy {base[:8]} -> {new[:8]}: {len(actions)} to apply, {len(skipped)} skipped")
        for op, p, _ in actions:
            say(f"  {op:7s} {p}")
        for p in paths:
            if p in DEPENDENCY_FILES:
                say(f"  WARNING {p} changed: the venv is NOT updated by this deploy; "
                    "a missing package fails the health check and rolls back")
        # 3. build every Next app whose source moved — before anything is touched
        for app, unit, port in NEXT_APPS:
            if any(p.startswith(app + "/") for p in paths) and (R / app).is_dir():
                builds.append((app, unit, port, build_next(app, new, actions, R, tag)))

        # 4. files
        for op, path, mode in actions:
            dst = R / path
            if dst.is_file():
                saved = bk / path
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, saved)
                undo.append(lambda d=dst, s=saved: (d.parent.mkdir(parents=True, exist_ok=True), shutil.copy2(s, d)))
            else:
                undo.append(lambda d=dst: d.unlink(missing_ok=True))
            if op == "delete":
                dst.unlink()
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".pivot-release-tmp")
            tmp.write_bytes(blob(new, path))
            os.chmod(tmp, mode)
            if os.geteuid() == 0:
                os.chown(tmp, 0, 0)
            os.replace(tmp, dst)
        installed = [p for op, p, _ in actions if op == "install"]
        py = [str(R / p) for p in installed if p.endswith(".py")]
        if py:
            run([PYTHON, "-m", "py_compile", *py])
        js = [str(R / p) for p in installed if p.startswith("charto/preview/") and p.endswith(".js")]
        for f in js:
            run(["node", "--check", f])
        stale = [p for p in installed if p.startswith("charto/preview/js/")]
        if stale and "charto/preview/index.html" not in installed:
            say("  WARNING preview JS changed but index.html did not: browsers may keep the old file (?v= stamp)")

        for unit, prefixes, url in SERVICES:
            if any(p.startswith(prefixes) for p in paths):
                say(f"  restart {unit}")
                restarted.append(unit)
                systemctl("restart", unit)
                if not healthy(url):
                    raise Failed(f"{unit} unhealthy after restart")

        # 5. swap in each build
        for app, unit, port, B in builds:
            live, prev = R / app / ".next", R / app / f".next.before-{tag}"
            if prev.exists():
                shutil.rmtree(prev)
            os.replace(live, prev)
            os.replace(B / ".next", live)
            undo.append(lambda l=live, p=prev, u=unit: (shutil.rmtree(l), os.replace(p, l), systemctl("restart", u)))
            say(f"  restart {unit}")
            systemctl("restart", unit)
            if not healthy(f"http://127.0.0.1:{port}/", ok=lambda c: 200 <= c < 400):
                raise Failed(f"{unit} unhealthy after swap")
    except Exception as exc:               # noqa: BLE001 — every failure unwinds
        msg = str(exc) if isinstance(exc, Failed) else f"{type(exc).__name__}: {exc}"
        say(f"FAILED {new[:8]}: {msg} — rolling back {len(undo)} step(s)")
        for step in reversed(undo):
            try:
                step()
            except Exception as e:         # noqa: BLE001 — keep unwinding
                say(f"  rollback step failed: {e}")
        state["failed"] = {"commit": new, "at": int(time.time()), "error": msg}
        return state
    finally:
        shutil.rmtree(BUILD_ROOT / f"build-{tag}", ignore_errors=True)

    # Keep exactly one previous .next per app, the one this deploy replaced.
    for app, _, _, _ in builds:
        for old in (R / app).glob(".next.before-*"):
            if old.name != f".next.before-{tag}":
                shutil.rmtree(old, ignore_errors=True)
    state.update(deployed=new, deployed_at=int(time.time()))
    state.pop("failed", None)
    with (STATE / "history.log").open("a") as h:
        h.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {base[:12]} -> {new[:12]} "
                f"{len(actions)} files\n")
    say(f"LIVE {new[:8]}")
    return state


def main(argv: list[str]) -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0                            # a deploy (a build) is still running
    state = load_state()
    if "--status" in argv:
        say(json.dumps(state, indent=2))
        return 0
    if "--retry" in argv:
        state.pop("failed", None)
        save_state(state)
    base = state.get("deployed")
    if not base:
        say("no deployed commit recorded in state.json; refusing to guess")
        return 1
    new = fetch()
    if new is None:
        return 0                            # branch not on GitHub yet: nothing to follow
    if new == base:
        return 0
    if git("cat-file", "-e", base + "^{commit}", check=False).returncode:
        git("fetch", "--quiet", "--filter=blob:none", "origin", base, check=False)
    if "--plan" in argv:
        actions, skipped = plan(base, new, release())
        say(f"{base[:8]} -> {new[:8]}")
        for op, p, _ in actions:
            say(f"  {op:7s} {p}")
        for p, why in skipped:
            say(f"  skip    {p}  ({why})")
        return 0
    if state.get("failed", {}).get("commit") == new:
        return 0                            # already failed; wait for a new commit or --retry
    state = deploy(base, new, state)
    save_state(state)
    return 1 if state.get("failed", {}).get("commit") == new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
