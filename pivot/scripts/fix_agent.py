#!/usr/bin/env python3
"""
Agent B — Automated Fixer
Reads /tmp/pivot_qa_log.json from Agent A and applies targeted fixes.

Run after Agent A:
  python scripts/fix_agent.py
"""

import json
import re
import sys
from pathlib import Path

LOG_FILE = Path("/tmp/pivot_qa_log.json")
ROOT = Path(__file__).parent.parent
FRONTEND = ROOT.parent / "frontend"


def load_log() -> dict:
    if not LOG_FILE.exists():
        print("No QA log found. Run Agent A first: python scripts/qa_agent.py")
        sys.exit(1)
    return json.loads(LOG_FILE.read_text())


def has_failure(results: list, pattern: str) -> bool:
    return any(
        any(pattern.lower() in f.lower() for f in r["failures"])
        for r in results if not r["passed"]
    )


def fix_strip_think_blocks(results: list):
    """Ensure _strip_think_blocks is applied to every Sarvam response."""
    if not has_failure(results, "<think>"):
        print("  [skip] strip-think-blocks: no <think> leaks detected")
        return False

    print("  [fix]  strip-think-blocks: patching sarvam_client + chat router")
    sarvam = ROOT / "backend" / "agents" / "sarvam_client.py"
    chat = ROOT / "backend" / "routers" / "chat.py"
    changed = False

    if sarvam.exists():
        c = sarvam.read_text()
        if "_strip_think_blocks" not in c:
            # Add helper at top
            inject = (
                "\nimport re\n\n_THINK_BLOCK_RE = re.compile(r\"<think>.*?</think>\\s*\", re.DOTALL | re.IGNORECASE)\n\n"
                "def _strip_think_blocks(text: str) -> str:\n"
                "    if not text: return text\n"
                "    return _THINK_BLOCK_RE.sub(\"\", text).strip()\n"
            )
            c = c.replace("logger = logging.getLogger(__name__)", "logger = logging.getLogger(__name__)\n" + inject)
            sarvam.write_text(c); changed = True
        if "_strip_think_blocks(content)" not in c and "_strip_think_blocks(data" not in c:
            c = c.replace(
                'return data["choices"][0]["message"]["content"]',
                'content = data["choices"][0]["message"]["content"] or ""\n                return _strip_think_blocks(content)',
            )
            sarvam.write_text(c); changed = True

    if chat.exists():
        c = chat.read_text()
        if "_strip_think_blocks" not in c:
            c = c.replace(
                "from backend.agents.sarvam_client import call_sarvam",
                "from backend.agents.sarvam_client import call_sarvam, _strip_think_blocks",
            )
            chat.write_text(c); changed = True
        if '"response": _strip_think_blocks(response)' not in c and '"response": response' in c:
            c = c.replace('"response": response,', '"response": _strip_think_blocks(response),')
            chat.write_text(c); changed = True

    return changed


def fix_truncated_think_blocks(results: list):
    """Handle truncated <think> blocks (no closing tag — happens when max_tokens cuts mid-think)."""
    leaks = [
        r for r in results
        if not r["passed"]
        and any("<think>" in f.lower() for f in r["failures"])
        and "</think>" not in r["response"].lower()
    ]
    if not leaks:
        print("  [skip] truncated-think: no orphan <think> openings detected")
        return False

    print("  [fix]  truncated-think: extending strip to handle unterminated <think> blocks")
    sarvam = ROOT / "backend" / "agents" / "sarvam_client.py"
    if not sarvam.exists(): return False
    c = sarvam.read_text()
    if "_TRUNCATED_THINK_RE" in c:
        return False
    inject = (
        "_TRUNCATED_THINK_RE = re.compile(r\"<think>.*\", re.DOTALL | re.IGNORECASE)\n\n"
        "def _strip_truncated_think(text: str) -> str:\n"
        "    \"\"\"Strip an unterminated <think>...EOF block (Sarvam truncates when hitting max_tokens).\"\"\"\n"
        "    if not text or \"</think>\" in text.lower(): return text\n"
        "    return _TRUNCATED_THINK_RE.sub(\"\", text).strip()\n\n"
    )
    c = c.replace(
        "def _strip_think_blocks(text: str) -> str:",
        inject + "def _strip_think_blocks(text: str) -> str:",
    )
    # Apply truncation strip after the normal strip
    c = c.replace(
        "    return _THINK_BLOCK_RE.sub(\"\", text).strip()",
        "    cleaned = _THINK_BLOCK_RE.sub(\"\", text).strip()\n    return _strip_truncated_think(cleaned)",
    )
    sarvam.write_text(c)
    return True


def fix_empty_response_after_strip(results: list):
    """Detect when stripping <think> left us with empty content — auto-retry with bigger budget."""
    empty = [r for r in results if not r["passed"] and r["response_length"] == 0]
    if not empty:
        print("  [skip] empty-after-strip: no zero-length responses")
        return False

    sarvam = ROOT / "backend" / "agents" / "sarvam_client.py"
    if not sarvam.exists(): return False
    c = sarvam.read_text()
    if "Empty response after think-strip" in c:
        print("  [skip] empty-after-strip: retry-on-empty already wired")
        return False

    print("  [fix]  empty-after-strip: adding auto-retry with 3x token budget")
    old = (
        '                content = data["choices"][0]["message"]["content"] or ""\n'
        '                return _strip_think_blocks(content)'
    )
    new = (
        '                content = data["choices"][0]["message"]["content"] or ""\n'
        '                cleaned = _strip_think_blocks(content)\n'
        '                if not cleaned and "<think>" in content.lower() and payload["max_tokens"] < 1600:\n'
        '                    payload["max_tokens"] = min(payload["max_tokens"] * 3, 2400)\n'
        '                    logger.warning("Empty response after think-strip; retrying with max_tokens=%d", payload["max_tokens"])\n'
        '                    continue\n'
        '                return cleaned'
    )
    if old in c:
        c = c.replace(old, new)
        sarvam.write_text(c)
        return True
    return False


def fix_response_length(results: list):
    """Cap chat max_tokens at 400."""
    if not has_failure(results, "TOO LONG"):
        print("  [skip] response-length: all responses within budget")
        return False

    print("  [fix]  response-length: forcing max_tokens=400 in chat router")
    chat = ROOT / "backend" / "routers" / "chat.py"
    if not chat.exists(): return False
    c = chat.read_text()
    new_c = re.sub(r"max_tokens\s*=\s*\d+", "max_tokens=400", c, count=1)
    if new_c != c:
        chat.write_text(new_c)
        return True
    return False


def fix_markdown_leak(results: list):
    """Tighten system prompt to forbid ** and ## explicitly when leaks happen."""
    if not has_failure(results, "MARKDOWN LEAK"):
        print("  [skip] markdown-leak: no ** or ## leaks detected")
        return False

    print("  [fix]  markdown-leak: reinforcing 'no markdown' rule in system prompt")
    chat = ROOT / "backend" / "routers" / "chat.py"
    if not chat.exists(): return False
    c = chat.read_text()
    # Idempotent — only insert if not already present
    marker = "ABSOLUTELY NO ASTERISKS OR HASH SYMBOLS"
    if marker in c: return False
    c = c.replace(
        "No markdown formatting in plain text responses.",
        "No markdown formatting in plain text responses. ABSOLUTELY NO ASTERISKS OR HASH SYMBOLS in any response.",
    )
    chat.write_text(c)
    return True


def fix_disclaimer_missing(results: list):
    """If T06-style disclaimer test fails, append a stronger reminder."""
    missing_disclaimer = any(
        r["test_id"] == "T06" and not r["passed"]
        for r in results
    )
    if not missing_disclaimer:
        print("  [skip] disclaimer: present where required")
        return False

    print("  [fix]  disclaimer: strengthening disclaimer rule")
    chat = ROOT / "backend" / "routers" / "chat.py"
    if not chat.exists(): return False
    c = chat.read_text()
    marker = "DISCLAIMER ALWAYS"
    if marker in c: return False
    c = c.replace(
        "DISCLAIMER — append to every response that proposes an order or strategy:",
        "DISCLAIMER ALWAYS — when the user asks for advice, opinions on stocks, or any directional view, append:",
    )
    chat.write_text(c)
    return True


def fix_hallucination(results: list):
    """If model hallucinates live prices, strengthen the no-data rule."""
    bad = any(r["test_id"] == "T10" and not r["passed"] for r in results)
    if not bad:
        print("  [skip] hallucination: live-data redirect working")
        return False

    print("  [fix]  hallucination: strengthening no-live-data rule")
    chat = ROOT / "backend" / "routers" / "chat.py"
    if not chat.exists(): return False
    c = chat.read_text()
    marker = "NEVER QUOTE LIVE PRICES"
    if marker in c: return False
    c = c.replace(
        "Never fabricate live prices, index levels, or market data you do not have.",
        "NEVER QUOTE LIVE PRICES OR INDEX LEVELS. If asked, say: 'I do not have real-time data — please check the live feed in your terminal.' Do not guess, do not estimate.",
    )
    chat.write_text(c)
    return True


def fix_must_contain_missing(results: list):
    """When required keywords are missing, this is a soft signal — log only.
    The system prompt is the right knob; no automatic patch we can make safely."""
    missing = [r for r in results if not r["passed"] and any("MISSING REQUIRED" in f or "MISSING (any)" in f for f in r["failures"])]
    if not missing:
        return False
    print(f"  [info] must-contain: {len(missing)} test(s) missing required keywords — manual prompt tuning may be needed")
    return False


def run_all_fixes():
    print("\n" + "=" * 60)
    print("PIVOT FIX AGENT — applying patches")
    print("=" * 60)

    log = load_log()
    results = log["results"]

    print(f"\nLoaded log: {LOG_FILE}")
    print(f"Pass rate before fixes: {log['pass_rate']}\n")

    any_change = False
    any_change |= fix_strip_think_blocks(results)
    any_change |= fix_truncated_think_blocks(results)
    any_change |= fix_empty_response_after_strip(results)
    any_change |= fix_response_length(results)
    any_change |= fix_markdown_leak(results)
    any_change |= fix_disclaimer_missing(results)
    any_change |= fix_hallucination(results)
    fix_must_contain_missing(results)

    print("\n" + "=" * 60)
    if any_change:
        print("Patches applied. Restart backend, then re-run qa_agent.")
    else:
        print("No code patches applied — failures are prompt/quality issues, not code bugs.")
    return any_change


if __name__ == "__main__":
    run_all_fixes()
