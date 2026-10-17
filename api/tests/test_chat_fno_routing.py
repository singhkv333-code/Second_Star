"""F&O P1 — chat routing regression: the decline fast-path is GONE,
every former decline trigger phrase routes to the options tools, the
tool handlers emit cards, and amendments stash a compact spec."""
import pytest

from backend.market.instrument_master import refresh_instrument_master


@pytest.fixture(autouse=True)
def _master_and_cache(db):
    # Flush optchain:* on BOTH MockRedis and real Redis — a dev server
    # on the same local Redis shares the keyspace (flake source).
    from backend.cache import redis_client

    if hasattr(redis_client, "_store"):
        redis_client._store.clear()
        redis_client._expires_at.clear()
    elif hasattr(redis_client, "scan_iter"):
        for key in list(redis_client.scan_iter("optchain:*")):
            redis_client.delete(key)
    refresh_instrument_master(db)
    yield


# Every phrase the old _FO_STRATEGY_RE used to hard-decline. The
# replacement requirement: the options tool surface must be routed in,
# and nothing may emit the old "isn't wired" decline.
_FORMER_DECLINE_PHRASES = [
    "sell a naked put on NIFTY",
    "covered call on my RELIANCE shares",
    "protective put for my portfolio",
    "cash secured put on BANKNIFTY",
    "build an iron condor",
    "iron butterfly on NIFTY",
    "bull call spread on NIFTY",
    "bear put spread please",
    "short strangle on BANKNIFTY",
    "long straddle into the RBI meet",
    "calendar spread on NIFTY",
    "sell a call option at 24000",
    "buy a put option on RELIANCE",
    "write a put below the market",
]


def test_decline_fast_path_is_gone():
    import backend.services.chat_service as cs

    assert not hasattr(cs, "_fo_strategy_decline")
    assert not hasattr(cs, "_FO_STRATEGY_RE")


@pytest.mark.parametrize("phrase", _FORMER_DECLINE_PHRASES)
def test_former_decline_phrases_route_to_options_tools(phrase):
    from backend.services.tool_router import select_tool_names

    selected = select_tool_names(phrase)
    assert "suggest_option_strategy" in selected or \
        "build_option_strategy" in selected, phrase


def test_mentions_fno_adds_options_tools_and_strips_orders():
    """The gate (chat_service handle()) must union _OPTIONS_TOOLS and
    remove equity order tools. Exercised structurally."""
    from backend.services.chat_service import _OPTIONS_TOOLS, _mentions_fno

    assert _mentions_fno("what about NIFTY options expiry this week?")
    assert _mentions_fno("F&O ban list")
    assert not _mentions_fno("buy 10 reliance shares")
    assert "suggest_option_strategy" in _OPTIONS_TOOLS


# ── Tool handler round-trips (mock chain, no LLM) ────────────────────


@pytest.mark.asyncio
async def test_get_option_chain_tool_emits_card(db):
    from backend.services.tool_registry import execute

    res = await execute(
        "get_option_chain", {"underlying": "NIFTY", "width": 5},
        kite_token="mock", db=db, user_id=1,
    )
    assert res.success, res.error
    assert res.data["_render_hint"] == "option_chain_card"
    assert len(res.data["rows"]) == 11
    assert "9 out of 10" in res.data["disclosure"]


@pytest.mark.asyncio
async def test_suggest_tool_emits_strategy_card(db):
    from backend.services.tool_registry import execute

    res = await execute(
        "suggest_option_strategy",
        {"underlying": "NIFTY", "view": "bearish"},
        kite_token="mock", db=db, user_id=1,
    )
    assert res.success, res.error
    data = res.data
    assert data["_render_hint"] == "option_strategy_card"
    assert data["summary"]["critique_verdict"] in ("ok", "caution", "risky")
    assert data["candidates"]
    assert data["editable"]["book"] == "paper"
    # The LLM-facing string keeps the decision-critical fields even after
    # the 6000-char truncation. Since the 51-sweep mock-data honesty fix,
    # a stale_note deliberately LEADS the JSON when the feed isn't live
    # (data honesty outranks everything), with summary right behind it.
    llm_view = res.to_llm_string()
    assert '"summary"' in llm_view[:500]
    if '"data_status": "mock"' in llm_view[:100]:
        assert '"stale_note"' in llm_view[:120], (
            "mock cards must lead with the stale warning"
        )


@pytest.mark.asyncio
async def test_build_tool_with_explicit_strikes(db):
    from backend.services.tool_registry import execute

    chain = await execute(
        "get_option_chain", {"underlying": "NIFTY", "width": 3},
        kite_token="mock", db=db, user_id=1,
    )
    atm = chain.data["atm_strike"]
    step = 50.0
    res = await execute(
        "build_option_strategy",
        {"underlying": "NIFTY", "template": "bull_call_spread",
         "strikes": [atm, atm + 2 * step]},
        kite_token="mock", db=db, user_id=1,
    )
    assert res.success, res.error
    strikes = [l["strike"] for l in res.data["editable"]["legs"]]
    assert strikes == [atm, atm + 2 * step]


@pytest.mark.asyncio
async def test_critique_tool_flags_naked_short(db):
    from backend.services.tool_registry import execute

    chain = await execute(
        "get_option_chain", {"underlying": "NIFTY", "width": 3},
        kite_token="mock", db=db, user_id=1,
    )
    atm = chain.data["atm_strike"]
    res = await execute(
        "critique_option_strategy",
        {"underlying": "NIFTY",
         "legs": [{"option_type": "PE", "side": "SELL", "strike": atm}]},
        kite_token="mock", db=db, user_id=1,
    )
    assert res.success, res.error
    # A naked short put is still a high-risk, undefended short — the
    # critique must flag it "risky". But its loss is FINITE (the strike
    # falling to zero), so max_loss is a real number, NEVER None/"unlimited".
    assert res.data["critique"]["verdict"] == "risky"
    ml = res.data["computed"]["max_loss"]
    assert ml is not None and ml > 0
    # The critique digest must lead with the bounded risk shape, never the
    # word "unlimited" for a put.
    digest = (res.data["critique"].get("digest") or "").lower()
    assert "unlimited" not in digest
    assert res.data["critique"].get("comparison")  # 2-row current-vs-alt


@pytest.mark.asyncio
async def test_portfolio_greeks_empty_then_aggregates(db):
    """P2 contract: the tool returns the portfolio_greeks_card shape
    (live re-mark when positions exist; registration-snapshot fallback
    for unfilled live-book intents)."""
    from backend.services.tool_registry import execute
    from backend.models import OptionStrategy, User
    from datetime import date

    res = await execute(
        "get_portfolio_greeks", {}, kite_token="mock", db=db, user_id=1,
    )
    assert res.success
    assert res.data["_render_hint"] == "portfolio_greeks_card"
    assert res.data["position_count"] == 0

    user = User(email="g@x.com", hashed_password="h")
    db.add(user)
    db.flush()
    db.add(OptionStrategy(
        user_id=user.id, underlying="NIFTY", segment="NFO-OPT",
        exchange="NSE", template="short_strangle",
        expiry=date.today(), book="live", status="registered",
        qty_lots=1, lot_size=65,
        net_greeks_json={"delta": -5.0, "gamma": -0.01,
                         "theta": 250.0, "vega": -200.0},
    ))
    db.flush()
    res2 = await execute(
        "get_portfolio_greeks", {}, kite_token="mock", db=db,
        user_id=user.id,
    )
    # Unfilled live-book intent → registration-snapshot fallback.
    assert res2.data["position_count"] == 1
    assert res2.data["net"]["theta"] == 250.0
    assert "NIFTY" in res2.data["by_underlying"]
    assert "snapshot" in res2.data["basis"]


def test_option_draft_spec_is_compact():
    """The amendment stash must stay well inside the 1800-char hint
    budget — the full card payload does not."""
    import json

    from backend.services.chat_service import _option_draft_spec

    fake_card = {
        "locked": {"underlying": "NIFTY", "expiry": "2026-06-09",
                   "lot_size": 65},
        "editable": {
            "template": "iron_condor", "qty_lots": 2,
            "legs": [
                {"option_type": "CE", "side": "SELL", "strike": 23750.0},
                {"option_type": "PE", "side": "SELL", "strike": 23200.0},
                {"option_type": "CE", "side": "BUY", "strike": 23900.0},
                {"option_type": "PE", "side": "BUY", "strike": 23050.0},
            ],
        },
        "computed": {"payoff": [{"s": i, "pnl": i} for i in range(61)]},
    }
    spec = _option_draft_spec(fake_card)
    assert spec["template"] == "iron_condor"
    assert len(spec["legs"]) == 4
    assert "payoff" not in json.dumps(spec)
    assert len(json.dumps(spec)) < 600
