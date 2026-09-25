"""The flexible engine: the user's words reach the model untouched.

The legacy router glued page context onto the front of the message, and ~40
regex detectors then read that string as if the user had typed it. 13 of 27
flipped their answer on ordinary messages; the portfolio value became "the
user stated ₹6,59,094". These tests pin the separation that fixes it.
"""
import asyncio

from backend.routers.chat import _page_context_lines
from backend.services import flex_chat
from backend.services.tool_registry import ToolResult

HOME = {
    "surface": "Home", "route": "/", "title": "Pivot — Agent System",
    "section": "value ₹6,59,094, 8 holdings, cash ₹11,633, paper book",
    "available_data": ["portfolio summary", "market movers"],
}


def test_page_context_is_its_own_block_not_part_of_the_message():
    lines = _page_context_lines(HOME, include_location=False)
    block = flex_chat.build_context(
        page_lines=lines, attachment_lines=[], quoted_text=None,
        mode=None, editor_draft=None, active_draft=None,
    )
    assert "₹6,59,094" in block and "Surface: Home" in block
    # Route and document title tell the model nothing the surface does not.
    assert "Route:" not in block and "Page title" not in block


def test_legacy_renderer_still_carries_location():
    lines = _page_context_lines(HOME, include_location=True)
    assert "- Route: /" in lines and any("Page title" in l for l in lines)


def test_the_brief_describes_targets_and_carries_the_non_negotiables():
    b = " ".join(flex_chat.BRIEF.split())  # the brief is hard-wrapped
    assert "analysis, not financial advice" in b
    assert "paper book" in b                     # simulate, don't execute
    assert "never written from memory" in b      # never fabricate
    assert "`source` is anything else" in b      # tag the relay when not Kite
    # A short brief is the point: the legacy core prompt was 994 lines.
    assert len(b.split()) < 600


def test_scripted_and_redundant_tools_are_withheld():
    names = {t.name for t in flex_chat._tooldefs()}
    assert "ASK_USER" not in names and "find_tool" not in names
    assert {"get_index_level", "get_top_movers", "propose_workflow"} <= names


def test_a_rejected_request_is_not_retried():
    """The legacy loop retried a 400 twice with 1.5s sleeps."""
    assert flex_chat._is_permanent(
        '{"error":{"message":"Unsupported parameter: \'temperature\'",'
        '"type":"invalid_request_error"}}')
    assert flex_chat._is_permanent("DeploymentNotFound")
    assert not flex_chat._is_permanent("upstream connect error or disconnect")
    assert not flex_chat._is_permanent("Read timed out")


def test_malformed_arguments_go_back_to_the_model():
    assert flex_chat._parse_args('{"symbol": "TCS"}') == ({"symbol": "TCS"}, None)
    args, err = flex_chat._parse_args('{"symbol": ')
    assert args == {} and "not valid JSON" in err
    assert flex_chat._parse_args("[1,2]")[1] == "arguments must be a JSON object"


def test_invalid_arguments_become_a_tool_error_not_a_user_question():
    """execute_with_completeness turned a missing field into a scripted
    question to the user; here the model reads the error and fixes its call."""
    schemas = {"get_index_level": {
        "type": "object", "properties": {"index": {"type": "string"}},
        "required": ["index"]}}
    r = asyncio.run(flex_chat._run_tool(
        "get_index_level", {}, schemas, ctx=None, own_session=False))
    assert isinstance(r, ToolResult) and not r.success
    assert "invalid arguments" in r.error
    unknown = asyncio.run(flex_chat._run_tool(
        "no_such_tool", {}, schemas, ctx=None, own_session=False))
    assert "unknown tool" in unknown.error


def test_card_is_found_at_top_level_or_one_down():
    top = {"_render_hint": "workflow_draft_card", "steps": []}
    assert flex_chat._card_of(top) is top
    nested = {"draft": {"_render_hint": "logic_card"}}
    assert flex_chat._card_of(nested) is nested["draft"]
    assert flex_chat._card_of({"rows": [1, 2]}) is None


def test_an_open_draft_is_shown_to_the_model_with_its_edit_anchor():
    """History is text-only, so without this 'make it 20 shares' is
    unanswerable; and without the anchor, Save creates a duplicate agent."""
    block = flex_chat.build_context(
        page_lines=[], attachment_lines=[], quoted_text=None, mode=None,
        editor_draft={"name": "RELIANCE open buyer", "workflow_id": "wf-42"},
        active_draft=None,
    )
    assert "Open in the strategy editor" in block
    assert "wf-42" in block and "propose_workflow" in block


def test_tool_start_subject_comes_from_the_models_own_arguments():
    """The loader says what is being checked; it must never guess."""
    assert flex_chat._subject({"index": "NIFTY 50"}) == "NIFTY 50"
    assert flex_chat._subject({"symbols": ["TCS", "INFY"]}) == "TCS and INFY"
    assert flex_chat._subject(
        {"symbols": ["TCS", "INFY", "WIPRO", "HCLTECH"]}) == "TCS, INFY and 2 more"
    assert flex_chat._subject({"symbol_a": "HDFCBANK", "symbol_b": "ICICIBANK"}) \
        == "HDFCBANK and ICICIBANK"
    assert flex_chat._subject({"direction": "gainers", "limit": 5}) == ""
    assert flex_chat._subject({"query": "x" * 60}).endswith("…")


def test_legacy_only_params_are_not_offered_to_the_flex_model():
    """presentation='table' promised "a deterministic table renders verbatim
    and you write nothing more"; flex renders no such table, so the model
    wrote one sentence pointing at nothing."""
    t = {x.name: x for x in flex_chat._tooldefs()}["screen_fundamentals"]
    assert "presentation" not in t.parameters["properties"]
    assert "total_matched" in t.parameters["properties"]["limit"]["description"]


def test_every_tool_is_loaded_or_one_search_away():
    """64 tools on the wire made every choice a 64-way one. Specialised groups
    now sit behind the provider's tool search; none may be lost on the way."""
    top, wire, schemas = flex_chat._tool_surface()
    namespaced = [f["name"] for w in wire if w["type"] == "namespace" for f in w["tools"]]
    assert all(f["defer_loading"] for w in wire if w["type"] == "namespace" for f in w["tools"])
    assert wire[-1] == {"type": "tool_search"}
    assert all(len(w["tools"]) <= 10 for w in wire if w["type"] == "namespace")
    names = [t.name for t in top] + namespaced
    assert sorted(names) == sorted(t.name for t in flex_chat._tooldefs())   # each once
    assert set(names) <= set(schemas)            # a deferred call still validates
    assert len(top) <= 25


def test_the_web_is_the_providers_search_not_a_snippet_api():
    names = {t.name for t in flex_chat._tooldefs()}
    assert "web_search_brief" not in names
    assert flex_chat.HOSTED_TOOLS and flex_chat.HOSTED_TOOLS[0]["type"] == "web_search"
    assert flex_chat.HOSTED_TOOLS[0]["user_location"]["country"] == "IN"


def test_citation_tokens_become_links_from_their_annotations():
    """'citeturn1search0' reached the reader verbatim; its URL was in an
    annotation the loop never read."""
    tok = "citeturn1search0"
    text = f"Dahej ran at 96.6%. {tok}"
    start = text.index(tok)
    out = flex_chat._with_links(text, [{
        "type": "url_citation", "url": "https://www.petronetlng.in/ar.pdf",
        "start_index": start, "end_index": start + len(tok)}])
    assert out == "Dahej ran at 96.6%. ([petronetlng.in](https://www.petronetlng.in/ar.pdf))"
    assert flex_chat._with_links(f"x {tok}", []) == "x "      # no URL: dropped
    assert flex_chat._with_links("plain", []) == "plain"
    # The model sometimes writes the token around a URL of its own.
    own = "p.48.\ue200cite\ue202https://nsearchives.nseindia.com/ar.pdf#page=48\ue201"
    assert flex_chat._with_links(own, []) == \
        "p.48. ([nsearchives.nseindia.com](https://nsearchives.nseindia.com/ar.pdf#page=48))"
