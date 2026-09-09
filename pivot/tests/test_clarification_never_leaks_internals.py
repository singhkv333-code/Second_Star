"""A clarification must never name something only the model can supply.

The failure this pins: a user asked "analyse my portfolio health", the model
reached for the `compute` sandbox and emitted it without its `code` argument,
and the completeness check turned that into a user-facing question — "Got it.
What's the code?" — asking a retail investor to supply Python.

The machinery was right that the call was unfillable and wrong about who could
fill it. These tests hold the line at the output: whatever the schema says is
missing, a question that reaches a user is about THEIR intent, in words they
can act on, and never about a tool argument.
"""
from __future__ import annotations

import pytest

from backend.services.validation_handler import (
    MissingField,
    _format_clarification_question,
)

# Tokens that mean the internals leaked into the product.
_INTERNAL = ("code", "steps", "payload", "params", "schema",
             "config", "json", "script", "field_name")


def _mk(name: str, desc: str = "", hint: str = "") -> MissingField:
    try:
        return MissingField(field_name=name, description=desc, type_hint=hint)
    except TypeError:                       # positional dataclass
        return MissingField(name, desc, hint)


@pytest.mark.parametrize("fields", [
    [_mk("code", "Python-subset script; the LAST expression's value is returned.")],
    [_mk("code"), _mk("steps")],
    [_mk("expression")], [_mk("payload")], [_mk("config")],
    [_mk("id")], [_mk("type")], [_mk("ref")],
])
def test_model_authored_and_opaque_fields_never_reach_the_user(fields):
    q = _format_clarification_question(fields).lower()
    leaked = [w for w in _INTERNAL if w in q]
    assert not leaked, f"leaked {leaked} in: {q}"
    # And it still asks for something, rather than going silent.
    assert q.strip().endswith("?")


def test_a_field_a_user_can_answer_is_still_asked_for_plainly():
    q = _format_clarification_question([_mk("symbol", "Stock ticker")])
    assert "?" in q
    assert not any(w in q.lower() for w in _INTERNAL)


def test_two_real_fields_still_ask_for_both():
    q = _format_clarification_question(
        [_mk("quantity", "How many shares"), _mk("symbol", "Stock ticker")]
    ).lower()
    assert "shares" in q and ("stock" in q or "ticker" in q or "etf" in q)


def test_the_prompt_also_forbids_asking_for_internals():
    """Belt and braces: the code guard is the floor, the rule is the ceiling.

    The guard can only sanitise a question the template built. Stopping the
    model from composing one in the first place is the prompt's job, so the
    rule has to actually be on the wire.
    """
    from backend.prompts import assembler
    core = assembler._load_chat_system_md()
    assert "Never ask the user for something only you can supply" in core
