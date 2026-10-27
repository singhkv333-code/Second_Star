"""Step 1 regression test: the canned 4-line marketing pitch must never appear.

This test does NOT hit the live LLM (it would be slow and flaky). Instead it
verifies the *upstream* fix: the canned line is no longer in the system
prompt, so the LLM can't be instructed to emit it.

The downstream regression test lives in `pivot-eval` — every full eval run
will fail any case whose response contains the canned pitch (auto_fail).
"""
from __future__ import annotations

from backend.prompts import system_prompt


CANNED = "Execute orders on Zerodha"
PRODUCT_HARDCODES = ["92.764", "0.92764", "0.07236", "7.236"]
PLACEHOLDER_TAGS = ["<LTP>", "<LTP_PREMIUM>", "<STRIKE>", "<STRIKE_LONG>",
                    "<STRIKE_SHORT>", "<PREMIUM>"]


def test_system_prompt_does_not_instruct_canned_pitch():
    text = system_prompt()
    assert CANNED not in text, (
        "The 4-line marketing pitch is still in the system prompt — "
        "the LLM will keep emitting it on greetings."
    )


def test_system_prompt_does_not_hardcode_product_economics():
    text = system_prompt()
    for token in PRODUCT_HARDCODES:
        assert token not in text, (
            f"Product economics ({token}) hardcoded in system prompt; "
            "they belong in config/products.yaml"
        )


_INSTRUCT_EMIT_PATTERNS = [
    "write the literal token",
    "use <STRIKE>",
    "use <PREMIUM>",
    "use the literal",
]


def test_system_prompt_does_not_instruct_placeholder_emission():
    """Placeholders may be NAMED in a forbidding clause; what we forbid is
    instructions to *emit* them. Check for instructional emission patterns."""
    text = system_prompt().lower()
    for pat in _INSTRUCT_EMIT_PATTERNS:
        assert pat not in text, (
            f"System prompt seems to instruct emission of placeholders: '{pat}'"
        )
