"""pivot_release against a real git repository and a fake trimmed release.

No systemd, no network: services are recorded instead of restarted, and
health is whatever the test says it is.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("pivot_release", Path(__file__).with_name("pivot_release.py"))
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, files: dict, msg: str, delete=()) -> str:
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    for rel in delete:
        (repo / rel).unlink()
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg)
    return sh(repo, "git", "rev-parse", "HEAD")


@pytest.fixture
def env(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    origin.mkdir()
    sh(origin, "git", "init", "-q", "-b", "production-live")
    sh(origin, "git", "config", "uploadpack.allowFilter", "true")
    sh(origin, "git", "config", "uploadpack.allowAnySHA1InWant", "true")
    base = commit(origin, {
        "charto/data/app.py": "VERSION = 1\n",
        "charto/data/gone.py": "x = 1\n",
        "charto/preview/js/main.js": "var a = 1;\n",
        "charto/README.md": "docs\n",
        "charto/web/.env.local": "SECRET=1\n",
        "pivot/backend/api.py": "A = 1\n",
        "pivot/tests/test_api.py": "def test(): pass\n",
    }, "base")
    rel = tmp_path / "release"
    for f in ("charto/data/app.py", "charto/data/gone.py", "charto/preview/js/main.js", "pivot/backend/api.py"):
        (rel / f).parent.mkdir(parents=True, exist_ok=True)
        (rel / f).write_text(sh(origin, "git", "show", f"{base}:{f}") + "\n")
    pointer = tmp_path / "stage-release"
    pointer.write_text(str(rel) + "\n")
    monkeypatch.setattr(pr, "REPO_URL", origin.as_uri())
    monkeypatch.setattr(pr, "MIRROR", tmp_path / "repo.git")
    monkeypatch.setattr(pr, "STATE", tmp_path / "state")
    monkeypatch.setattr(pr, "POINTER", pointer)
    monkeypatch.setattr(pr, "BUILD_ROOT", tmp_path / "builds")
    monkeypatch.setattr(pr, "BUILD_USER", "")
    monkeypatch.setattr(pr, "PYTHON", sys.executable)
    calls, health = [], {"ok": True}
    monkeypatch.setattr(pr, "systemctl", lambda *a: calls.append(a))
    monkeypatch.setattr(pr, "healthy", lambda url, **k: health["ok"])
    pr.STATE.mkdir()
    pr.save_state({"deployed": base})
    return dict(origin=origin, rel=rel, base=base, calls=calls, health=health)


def test_plan_applies_only_what_the_trimmed_release_carries(env):
    o = env["origin"]
    new = commit(o, {
        "charto/data/app.py": "VERSION = 2\n",          # modified, carried
        "charto/data/new_mod.py": "N = 1\n",            # added, carried
        "charto/README.md": "more docs\n",              # modified, release cut without it
        "charto/NOTES.md": "new top-level doc\n",       # added top-level doc
        "charto/web/.env.local": "SECRET=2\n",          # never shipped
        "pivot/tests/test_new.py": "x = 1\n",           # trimmed tree
        "docs/x.md": "outside\n",                       # outside the release
    }, "change", delete=["charto/data/gone.py"])
    pr.fetch()
    actions, skipped = pr.plan(env["base"], new, env["rel"])
    assert sorted((op, p) for op, p, _ in actions) == [
        ("delete", "charto/data/gone.py"), ("install", "charto/data/app.py"), ("install", "charto/data/new_mod.py")]
    reasons = dict(skipped)
    assert reasons["charto/README.md"] == "not in release"
    assert reasons["charto/NOTES.md"] == "top-level doc/config"
    assert reasons["charto/web/.env.local"] == "never shipped"
    assert reasons["pivot/tests/test_new.py"] == "trimmed tree"
    assert "docs/x.md" not in reasons            # not even diffed


def test_a_move_deploys_restarts_what_moved_and_records_it(env):
    new = commit(env["origin"], {"charto/data/app.py": "VERSION = 2\n"}, "v2", delete=["charto/data/gone.py"])
    assert pr.main([]) == 0
    rel = env["rel"]
    assert (rel / "charto/data/app.py").read_text() == "VERSION = 2\n"
    assert not (rel / "charto/data/gone.py").exists()
    assert pr.load_state()["deployed"] == new
    assert (pr.STATE / "backups" / new[:12] / "charto/data/app.py").read_text() == "VERSION = 1\n"
    assert env["calls"] == [("restart", "charto")]                # not pivot-api: pivot/ did not move
    assert pr.main([]) == 0 and env["calls"] == [("restart", "charto")]   # idle poll does nothing


def test_preview_only_change_restarts_nothing(env):
    commit(env["origin"], {"charto/preview/js/main.js": "var a = 2;\n"}, "fe")
    assert pr.main([]) == 0
    assert env["calls"] == []
    assert (env["rel"] / "charto/preview/js/main.js").read_text() == "var a = 2;\n"


def test_an_unhealthy_service_rolls_every_file_back_and_is_not_retried(env):
    rel = env["rel"]
    before = {f: (rel / f).read_text() for f in ("charto/data/app.py", "charto/data/gone.py", "pivot/backend/api.py")}
    new = commit(env["origin"], {"charto/data/app.py": "VERSION = 2\n", "charto/data/added.py": "Z = 1\n",
                                 "pivot/backend/api.py": "A = 2\n"}, "bad", delete=["charto/data/gone.py"])
    env["health"]["ok"] = False
    assert pr.main([]) == 1
    assert {f: (rel / f).read_text() for f in before} == before
    assert not (rel / "charto/data/added.py").exists()
    st = pr.load_state()
    assert st["deployed"] == env["base"] and st["failed"]["commit"] == new
    # restarted (charto, failed health) and then restarted again on the restored files
    assert env["calls"] == [("restart", "charto"), ("restart", "charto")]
    env["calls"].clear()
    assert pr.main([]) == 0 and env["calls"] == []              # same commit: left alone
    env["health"]["ok"] = True
    assert pr.main(["--retry"]) == 0                            # retry on request
    assert pr.load_state()["deployed"] == new
    assert (rel / "pivot/backend/api.py").read_text() == "A = 2\n"


def test_a_syntax_error_never_reaches_a_restart(env):
    commit(env["origin"], {"charto/data/app.py": "VERSION = (\n"}, "syntax")
    assert pr.main([]) == 1
    assert (env["rel"] / "charto/data/app.py").read_text() == "VERSION = 1\n"
    assert env["calls"] == []


def test_moving_the_branch_back_rolls_back(env):
    o = env["origin"]
    commit(o, {"charto/data/app.py": "VERSION = 2\n"}, "v2")
    assert pr.main([]) == 0
    sh(o, "git", "reset", "-q", "--hard", env["base"])
    assert pr.main([]) == 0
    assert (env["rel"] / "charto/data/app.py").read_text() == "VERSION = 1\n"
    assert pr.load_state()["deployed"] == env["base"]


def test_no_branch_on_the_remote_means_nothing_to_follow(env):
    sh(env["origin"], "git", "branch", "-m", "production-live", "other")
    assert pr.main([]) == 0
    assert pr.load_state() == {"deployed": env["base"]}
