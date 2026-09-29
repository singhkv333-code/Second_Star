"""The wire text for borrowed Pivot tools: consistent with Pivot's schema and
free of routes to tools this surface does not have."""
from __future__ import annotations

import json
import re

import execution_bridge as eb
import execution_tool_docs as docs

# Tools this surface has besides PIVOT_TOOLS that a description may name.
_CHARTO_TOOLS = {"register_plan", "set_alert", "save_strategy"}
_BANNED = (r"ASK_USER", r"ask_user", r"broker app", r"own broker", r"Pivot's",
           r"\boptions?\b", r"\bOptions?\b")


def _tools():
    assert eb.available()[0]
    return eb._state["mods"]["ALL_TOOLS"]


def test_every_overridden_param_exists_in_pivots_schema():
    allt = _tools()
    for name, ov in docs.OVERRIDES.items():
        props = allt[name]["function"]["parameters"]["properties"]
        for p in ov.get("params", {}):
            assert p in props, f"{name}.{p} is not in Pivot's schema"


def test_every_borrowed_tool_is_covered():
    assert set(docs.OVERRIDES) == set(eb.PIVOT_TOOLS)


def test_apply_changes_text_only():
    allt = _tools()
    for name in eb.PIVOT_TOOLS:
        orig = allt[name]["function"]
        before = json.dumps(orig, sort_keys=True)
        new = docs.apply(orig)
        assert json.dumps(orig, sort_keys=True) == before      # not mutated
        assert new["name"] == orig["name"]
        op, np_ = orig["parameters"], new["parameters"]
        assert op.get("required") == np_.get("required")
        assert set(op["properties"]) == set(np_["properties"])
        for k, v in op["properties"].items():
            for field in ("type", "enum", "items", "minimum", "default"):
                assert v.get(field) == np_["properties"][k].get(field)


def test_wire_text_names_no_absent_tool_and_no_banned_route():
    allt = _tools()
    present = set(eb.PIVOT_TOOLS) | _CHARTO_TOOLS
    absent = [n for n in allt if n not in present]
    for name in eb.PIVOT_TOOLS:
        new = docs.apply(allt[name]["function"])
        blob = new["description"] + " ".join(
            p.get("description", "") for p in new["parameters"]["properties"].values())
        for n in absent:
            assert not re.search(r"(?<![A-Za-z_.])" + re.escape(n) + r"(?![A-Za-z_])", blob), \
                f"{name} still names absent tool {n}"
        for b in _BANNED:
            assert not re.search(b, blob), f"{name} contains {b!r}"


def test_propose_workflow_keeps_a_filtered_step_catalog():
    allt = _tools()
    d = docs.apply(allt["propose_workflow"]["function"])["description"]
    assert "{catalog}" not in d and "{step_notes}" not in d
    assert "action.place_order [ACT] req: side,symbol" in d
    assert "trigger.compound [TRG]" in d
    assert "place_option_strategy" not in d and "arm_ipo_intent" not in d
    assert "trigger.expiry_day" not in d and "trigger.ipo_open" not in d


def test_descriptions_stay_short():
    allt = _tools()
    for name in eb.PIVOT_TOOLS:
        n = len(docs.apply(allt[name]["function"])["description"])
        limit = 5200 if name == "propose_workflow" else 2500
        assert n <= limit, (name, n)


def test_a_tool_without_an_override_is_returned_unchanged():
    d = {"name": "something_else", "description": "x", "parameters": {}}
    assert docs.apply(d) is d
