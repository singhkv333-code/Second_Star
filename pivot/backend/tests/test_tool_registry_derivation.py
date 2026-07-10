"""Chat-kernel Phase 0 tripwire: the DERIVED visible-tool set must match
the legacy hand-maintained list exactly.

If this test fails after you added a tool: you probably implemented a
real handler (tool becomes visible automatically — update the snapshot)
or wired a stub (keep it invisible — no snapshot change needed). The
snapshot exists so visibility changes are always a conscious diff, never
an accident.
"""
from backend.services.tool_registry import (
    _HIDDEN_TOOLS,
    _REAL_TOOLS_LEGACY_SNAPSHOT,
    _real_tools,
    get_tool_schema,
)


def test_derived_set_matches_legacy_snapshot():
    derived = _real_tools()
    added = derived - _REAL_TOOLS_LEGACY_SNAPSHOT
    lost = _REAL_TOOLS_LEGACY_SNAPSHOT - derived
    assert not added and not lost, (
        f"visible-tool derivation drifted: added={sorted(added)} "
        f"lost={sorted(lost)}"
    )


def test_stubs_and_hidden_are_invisible():
    from backend.agents.tool_executor import STUB_TOOLS
    from backend.services.tool_registry import _V2_HANDLERS

    derived = _real_tools()
    # A legacy stub may be superseded by a real v2 handler (e.g.
    # get_52wk_range) — those are legitimately visible. Pure stubs are not.
    pure_stubs = STUB_TOOLS - set(_V2_HANDLERS)
    assert not (pure_stubs & derived), "stub tools leaked into the visible set"
    assert not (_HIDDEN_TOOLS & derived), "hidden tools leaked into the visible set"


def test_schema_only_contains_visible_tools():
    names = {d["function"]["name"] for d in get_tool_schema()}
    assert names <= _real_tools()
    # Every visible name that has a schema is present (schema-less
    # handlers simply don't render — that's fine, not a drift).
    from backend.agents.tools import ALL_TOOLS

    assert names == {n for n in _real_tools() if n in ALL_TOOLS}


def test_every_visible_tool_dispatches_to_a_handler():
    """A visible tool with no execution path is the drift class this
    refactor exists to kill — assert it can't happen."""
    from backend.agents.tool_executor import HANDLERS
    from backend.services.tool_registry import _V2_HANDLERS

    for name in _real_tools():
        assert name in HANDLERS or name in _V2_HANDLERS, (
            f"{name} is visible but has no handler"
        )
