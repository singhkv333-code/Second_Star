"""Tests for the /api/views router (View Markets V2).

Conftest gives us a shared in-memory SQLite DB + an authed client. The test DB
is empty by default, so each test seeds the minimal MarketView /
ViewExpression / ViewConfidence rows it needs.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.config import settings
from backend.models import (
    ConfidenceDimension,
    ExpressionKind,
    ExpressionTier,
    MarketView,
    ViewConfidence,
    ViewExpression,
    ViewStatus,
    ViewTransmission,
    ViewType,
)


@pytest.fixture(autouse=True)
def _enable_flag(monkeypatch):
    """Each test starts with the flag ON; individual tests flip it OFF."""
    monkeypatch.setattr(settings, "view_markets_enabled", True)


@pytest.fixture(autouse=True)
def _fresh_views_cache():
    """GET /api/views (list) and GET /api/views/{id} (detail) are Redis-cached
    (short TTL — see backend/routers/views.py). Flush both prefixes between
    tests: several tests here seed a MarketView under a FIXED curated id
    (``IT_ID`` / ``CRUDE_ID``) with different row content per test, and a
    real local Redis (redis://localhost:6379/0, shared — see conftest) would
    otherwise serve one test's cached response to the next (order-dependent
    flakes). Mirrors the equivalent fixture in test_option_chain.py."""
    from backend.cache import redis_client

    def _flush() -> None:
        if hasattr(redis_client, "_store"):  # MockRedis
            redis_client._store.clear()
            redis_client._expires_at.clear()
        elif hasattr(redis_client, "scan_iter"):  # real Redis
            for prefix in ("views:list:v1:*", "views:detail:v1:*"):
                for key in list(redis_client.scan_iter(prefix)):
                    redis_client.delete(key)

    _flush()
    yield
    _flush()


def _seed_view(
    db,
    *,
    title="IT giants weak guidance",
    view_type=ViewType.event,
    status=ViewStatus.developing,
    category="equity_rotation",
    with_scored_expression=True,
):
    v = MarketView(
        title=title,
        view_type=view_type,
        status=status,
        category=category,
        thesis="thesis text",
        time_horizon="3-6m",
    )
    db.add(v)
    db.flush()
    if with_scored_expression:
        e = ViewExpression(
            view_id=v.id,
            tier=ExpressionTier.conservative,
            expression_kind=ExpressionKind.basket,
            rationale="Defence + Auto basket",
            risk_profile="defensive",
            capital_intensity="moderate",
            historical_strength="strong",
            time_horizon="3-6m",
            config={
                "label": "Cons basket",
                "instruments": [{"symbol": "HAL", "segment": "NSE"}],
                "warnings": [],
                "disclaimer": "Analysis only — register-not-execute.",
                "structure": {
                    "scheme": "equal_weight",
                    "weights": {"HAL": 0.5, "BEL": 0.5},
                },
                "scores": {
                    "alignment_kind": "event_study",
                    "construction_alignment": 78,
                    "backtest": {
                        "grade": "A",
                        "trust_verdict": "promising",
                        "trust_conf": 82,
                        "total_return_pct": 14.2,
                        "excess_return_pct": 6.8,
                        "expression_score": 76,
                        "outcome_score": None,
                    },
                },
            },
        )
        db.add(e)
        db.flush()
    db.add(
        ViewConfidence(
            view_id=v.id,
            dimension=ConfidenceDimension.outcome,
            score=0.72,
            evidence="analog hit-rate 70%",
        )
    )
    db.add(
        ViewConfidence(
            view_id=v.id,
            dimension=ConfidenceDimension.expression,
            score=0.66,
            evidence="CAAR alignment 71%",
        )
    )
    db.add(
        ViewTransmission(
            view_id=v.id,
            seq=0,
            from_node="weak_guidance",
            to_node="it_sector",
            edge_label="margin_compression",
            strength=0.7,
            evidence="IT majors -3% on guidance",
        )
    )
    db.flush()
    return v


# ── list ────────────────────────────────────────────────────────────────────


def test_list_returns_seeded_views(client, db):
    _seed_view(db, title="View A")
    _seed_view(db, title="View B", view_type=ViewType.theme)
    db.commit()
    r = client.get("/api/views")
    assert r.status_code == 200, r.text
    body = r.json()
    titles = [it["title"] for it in body["items"]]
    assert "View A" in titles and "View B" in titles
    # best_expression projection from config.scores
    a = next(it for it in body["items"] if it["title"] == "View A")
    assert a["best_expression"] is not None
    assert a["best_expression"]["grade"] == "A"
    assert a["best_expression"]["trust_verdict"] == "promising"
    assert a["outcome_confidence"]["score"] == 72
    assert a["outcome_confidence"]["letter"] in {"B", "C"}  # 72 -> B (>=70)
    assert a["expression_count"] == 1
    assert a["transmission_count"] == 1


def test_list_filter_unknown_status_422(client, db):
    _seed_view(db)
    db.commit()
    r = client.get("/api/views?status=bogus")
    assert r.status_code == 422


# ── detail ──────────────────────────────────────────────────────────────────


def test_detail_projects_scores_and_confidence(client, db):
    v = _seed_view(db)
    db.commit()
    r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == v.id
    # Confidence letters via API contract bands (72->B, 66->C).
    assert body["confidence"]["outcome"]["score"] == 72
    assert body["confidence"]["outcome"]["letter"] == "B"
    assert body["confidence"]["expression"]["score"] == 66
    assert body["confidence"]["expression"]["letter"] == "C"
    # Expression projection: scores + structure passthrough.
    exprs = body["expressions"]
    assert len(exprs) == 1
    e = exprs[0]
    assert e["scores"]["backtest"]["trust_verdict"] == "promising"
    assert e["structure"]["scheme"] == "equal_weight"
    assert e["is_deployable"] is True
    # Transmission edges.
    assert body["transmission"][0]["from_node"] == "weak_guidance"


def test_detail_missing_scores_returns_null(client, db):
    v = MarketView(
        title="No-score view", view_type=ViewType.event,
        status=ViewStatus.developing,
    )
    db.add(v)
    db.flush()
    e = ViewExpression(
        view_id=v.id, tier=ExpressionTier.conservative,
        expression_kind=ExpressionKind.basket, config={},
    )
    db.add(e)
    db.commit()
    r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expressions"][0]["scores"] is None
    assert body["best_expression"] is None


def test_detail_unknown_id_404(client, db):
    r = client.get("/api/views/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "not_found"


# ── flag gate ───────────────────────────────────────────────────────────────


def test_flag_off_returns_404(client, db, monkeypatch):
    monkeypatch.setattr(settings, "view_markets_enabled", False)
    r = client.get("/api/views")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body and body["error"]["code"] == "not_found"


# ── deploy: reuses linked workflow without placing an order ─────────────────


def test_deploy_returns_linked_workflow_id_without_broker_call(
    client, auth_headers, db,
):
    # Seed a view + expression already linked to a real Workflow row.
    from backend.models import Workflow, WorkflowStatus

    v = _seed_view(db, with_scored_expression=True)
    # Find the seeded expression and link a draft workflow to it.
    expr = (
        db.query(ViewExpression)
        .filter(ViewExpression.view_id == v.id)
        .one()
    )
    wf = Workflow(
        user_id=1,
        name="armed draft",
        description="armed, not executed",
        status=WorkflowStatus.draft,
        single_instance=True,
    )
    db.add(wf)
    db.flush()
    expr.workflow_id = str(wf.id)
    db.commit()

    # Trip-wire: deploy must NEVER place an order. Patch the broker seam.
    with patch("backend.routers.orders.submit_order_for_user") as broker, \
         patch(
             "backend.view_markets.deployment.deploy.deploy_expression"
         ) as deployer:
        # activate=False with a linked workflow short-circuits — neither the
        # broker nor the deployer should be touched.
        r = client.post(
            f"/api/views/expressions/{expr.id}/deploy",
            json={"activate": False},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["workflow_id"] == str(wf.id)
        assert body["activated"] is False
        assert body["status"] == "draft"
        broker.assert_not_called()
        deployer.assert_not_called()


def test_deploy_unknown_expression_404(client, db):
    r = client.post(
        "/api/views/expressions/no-such-id/deploy",
        json={"activate": False},
    )
    assert r.status_code == 404


# ── follow / unfollow ───────────────────────────────────────────────────────


def test_follow_then_unfollow(client, auth_headers, db):
    v = _seed_view(db, with_scored_expression=False)
    db.commit()
    r = client.post(f"/api/views/{v.id}/follow", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_following"] is True
    assert body["follower_count"] >= 1

    r = client.delete(f"/api/views/{v.id}/follow", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_following"] is False


# ── layman content layer ─────────────────────────────────────────────────────

# The three live, curated view ids (must match plain_copy.VIEW_COPY).
IT_ID = "4f40f896-0953-4d66-bf6f-1932667b531e"
CRUDE_ID = "19f04e99-b704-4166-b99a-697049885d44"


def _seed_curated_it(db):
    """Seed the IT view under its real curated id with two scored expressions:

    a CONSERVATIVE basket (the curated headline, ~flat Nifty, expression_score
    None) and an AGGRESSIVE hedge with a higher score — which the OLD auto-pick
    would have surfaced as the hero.
    """
    v = MarketView(
        id=IT_ID,
        title="India's IT giants are in trouble (weak guidance cycle)",
        view_type=ViewType.event,
        status=ViewStatus.developing,
        category="equity_rotation",
        thesis="raw jargon thesis CAAR t=2.00 p=0.046",
        time_horizon="4-8 weeks per event",
    )
    db.add(v)
    db.flush()
    db.add(
        ViewExpression(
            view_id=v.id,
            tier=ExpressionTier.conservative,
            expression_kind=ExpressionKind.basket,
            capital_intensity="low",
            time_horizon="4-8 weeks",
            config={
                "label": "Conservative — EW genuine-beneficiary basket",
                "instruments": [
                    {"symbol": "RECLTD.NS", "role": "long", "instrument_type": "equity"},
                ],
                "structure": {
                    "scheme": "equal_weight",
                    "n_names": 5,
                    "members_long": [
                        "RECLTD.NS", "ADANIPOWER.NS", "JPPOWER.NS",
                        "RVNL.NS", "ENGINERSIN.NS",
                    ],
                },
                "scores": {
                    "backtest": {
                        "grade": "C",
                        "trust_verdict": "unproven",
                        "total_return_pct": 48.83,
                        "nifty_total_pct": -4.85,
                        "excess_return_pct": 53.68,
                        "n_episodes": 8,
                        "pct_episodes_beat": 62.5,
                        "max_dd_pct": -13.92,
                        "expression_score": None,
                    },
                },
            },
        )
    )
    db.add(
        ViewExpression(
            view_id=v.id,
            tier=ExpressionTier.aggressive,
            expression_kind=ExpressionKind.hedge,
            capital_intensity="high",
            config={
                "label": "Aggressive — long basket / short NIFTY",
                "scores": {
                    "backtest": {
                        "grade": "B",
                        "trust_verdict": "unproven",
                        "total_return_pct": 56.92,
                        "nifty_total_pct": -4.85,
                        "excess_return_pct": 61.77,
                        "max_dd_pct": -11.79,
                        "expression_score": 71,
                    },
                },
            },
        )
    )
    db.flush()
    return v


def test_layman_fields_present_and_populated_for_curated_view(client, db):
    v = _seed_curated_it(db)
    db.commit()
    r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    body = r.json()

    # View-level plain copy (curated, de-jargoned — never the raw thesis).
    assert body["plain_one_liner"] and "weak quarter" in body["plain_one_liner"]
    assert body["plain_summary"]
    assert body["plain_thesis"].endswith("This is analysis, not financial advice.")
    assert "CAAR" not in body["plain_thesis"]
    assert body["benchmark_label"] == "Nifty 50"

    # HEADLINE: the hero leads with the HIGHEST-returning expression (product
    # decision — the gallery card + detail chart feature the best result), here
    # the aggressive hedge (+56.92%), not the lower conservative basket (+48.83%).
    be = body["best_expression"]
    assert be is not None
    assert be["tier"] == "aggressive"
    assert be["expression_kind"] == "hedge"
    assert be["total_return_pct"] == 56.92
    assert be["nifty_total_pct"] == -4.85
    assert be["worst_drop_pct"] == -11.79
    assert be["plain_label"]

    # Expression-level layman fields on the conservative basket.
    cons = next(e for e in body["expressions"] if e["tier"] == "conservative")
    assert cons["plain_one_liner"] and cons["plain_why"] and cons["plain_risk"]
    assert cons["capital_label"] == "Low"            # never a rupee figure
    assert cons["trust_badge"] == "Unproven"
    assert cons["members"] == [
        "REC", "Adani Power", "JP Power", "RVNL", "Engineers India",
    ]
    assert cons["n_names"] == 5
    assert cons["strategy_total_pct"] == 48.83
    assert cons["nifty_total_pct"] == -4.85
    assert cons["excess_return_pct"] == 53.68
    assert cons["worst_drop_pct"] == -13.92


def test_new_enrichment_fields_present_and_real_or_null(client, db):
    """short_title / strategy identity / option legs + REAL-or-empty curves.

    The seeded expressions use fresh ids that are NOT in the on-disk precompute
    cache, so the equity curve / holdings come back empty (honest), never a
    fabricated line — while the curated copy (short_title, strategy_name) still
    resolves off the view id + tier.
    """
    v = _seed_curated_it(db)
    db.commit()
    r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    body = r.json()

    # View-level crisp copy — a simple, dateless question.
    assert body["short_title"] == "Will weak IT results lift domestic stocks?"
    assert len(body["short_title"].split()) <= 8
    assert body["description"]
    assert len(body["bullets"]) == 3
    # Similar Views = the OTHER two curated ids.
    sim_ids = {s["id"] for s in body["similar_views"]}
    assert IT_ID not in sim_ids
    assert sim_ids == {CRUDE_ID, "81809245-feeb-4ead-9f35-eb8166757cb7"}
    # Fundamentals: honest null when the Moneycontrol benchmark isn't available.
    assert body["fundamental_comparison"] is None

    cons = next(e for e in body["expressions"] if e["tier"] == "conservative")
    # Honest, differentiated strategy identity (NOT "basket"/"basket").
    assert cons["strategy_name"] == "Proudly Homegrown bundle"
    assert cons["strategy_type"] == "Basket"
    assert cons["option_legs"] is None
    # The balanced/aggressive tiers are genuinely a DIFFERENT construction.
    aggr = next(e for e in body["expressions"] if e["tier"] == "aggressive")
    assert aggr["strategy_type"] == "Pair (market-neutral)"
    assert aggr["strategy_name"] != cons["strategy_name"]

    # Curves are real-or-empty — NEVER fabricated for an uncached expression id.
    for e in body["expressions"]:
        assert isinstance(e["equity_curve"], list)
        assert isinstance(e["holdings"], list)
        for p in e["equity_curve"]:
            assert "t" in p and "strategy" in p and "benchmark" in p
        # risk_return_ratio is a number or null, never a string.
        assert e["risk_return_ratio"] is None or isinstance(
            e["risk_return_ratio"], (int, float)
        )
    # best_expression gallery mini-line is a list (real-or-empty).
    assert isinstance(body["best_expression"]["equity_curve"], list)


def test_option_legs_built_for_option_expression(client, db):
    """An option_strategy expression serves concrete illustrative legs."""
    from backend.models import ExpressionKind, ExpressionTier

    v = MarketView(
        id="81809245-feeb-4ead-9f35-eb8166757cb7",  # curated Monsoon id
        title="Monsoon trade",
        view_type=ViewType.theme,
        status=ViewStatus.developing,
    )
    db.add(v)
    db.flush()
    db.add(
        ViewExpression(
            view_id=v.id,
            tier=ExpressionTier.aggressive,
            expression_kind=ExpressionKind.option_strategy,
            config={"structure": {"members_long": ["BRITANNIA.NS"]}},
        )
    )
    db.commit()
    r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    opt = next(
        e for e in body["expressions"] if e["expression_kind"] == "option_strategy"
    )
    assert opt["strategy_type"] == "Options (defined-risk)"
    assert opt["strategy_name"] == "Boldly Bullish call spread"
    assert opt["option_legs"] and len(opt["option_legs"]) == 2
    # Honest: legs are RULES (atm / delta), not invented numbers.
    actions = {leg["action"] for leg in opt["option_legs"]}
    assert actions == {"BUY", "SELL"}
    assert all(leg["option_type"] == "CE" for leg in opt["option_legs"])
    assert opt["option_legs_note"] and "deploy" in opt["option_legs_note"]


def test_layman_developing_view_has_no_hero(client, db):
    """A curated DEVELOPING view (headline_tier None) renders no hero number."""
    v = MarketView(
        id=CRUDE_ID,
        title="Crude de-escalation importer trade",
        view_type=ViewType.event,
        status=ViewStatus.developing,
    )
    db.add(v)
    db.flush()
    db.add(
        ViewExpression(
            view_id=v.id,
            tier=ExpressionTier.conservative,
            expression_kind=ExpressionKind.basket,
            config={
                "scores": {
                    "backtest": {
                        "total_return_pct": 24.54,
                        "nifty_total_pct": 14.4,
                        "expression_score": 80,  # would be auto-picked if not gated
                    },
                },
            },
        )
    )
    db.commit()
    r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    # Curated developing view -> no finished hero even though a scored expr exists.
    assert body["best_expression"] is None
    assert body["plain_thesis"] and "developing" in body["plain_thesis"]


def test_layman_unseeded_view_humanized_fallback(client, db):
    """A future / unseeded view never leaks a raw enum or jargon thesis."""
    v = _seed_view(db, title="Some brand-new belief", with_scored_expression=True)
    db.commit()
    r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    # Fallback: plain_one_liner = title; longer prose null-safe.
    assert body["plain_one_liner"] == "Some brand-new belief"
    assert body["plain_summary"] is None
    assert body["plain_thesis"] is None
    assert body["benchmark_label"] == "Nifty 50"
    # Expression layman fields are still generated (no invented numbers).
    e = body["expressions"][0]
    assert e["plain_one_liner"]  # generated plain projection
    assert e["trust_badge"] == "Promising"
    assert e["capital_label"] == "Low-medium"  # "moderate" -> Low-medium
    # Clean numbers still flow from the backtest block.
    assert e["strategy_total_pct"] == 14.2
    assert e["excess_return_pct"] == 6.8


# ── round-3 detail-page data (episodes / exit / alignment / holdings / MC) ────


def test_monte_carlo_terminal_distribution_real_and_deterministic():
    """The reused block-bootstrap engine yields an ordered terminal distribution."""
    from backend.services.backtest.validation.monte_carlo import (
        monte_carlo_terminal_distribution,
    )

    rets = [0.01, -0.005, 0.012, -0.02, 0.008, 0.003, -0.01, 0.015, 0.002, -0.004,
            0.006, -0.003]
    a = monte_carlo_terminal_distribution(rets, n_sims=500, n_points=50)
    b = monte_carlo_terminal_distribution(rets, n_sims=500, n_points=50)
    assert a is not None and a == b  # deterministic for a fixed seed
    assert a["n_sims"] == 500
    assert a["p05"] <= a["p25"] <= a["median"] <= a["p75"] <= a["p95"]
    assert 0.0 <= a["prob_loss"] <= 1.0
    assert len(a["terminal_pct"]) <= 50
    # too few observations -> honest None (never a fabricated spread)
    assert monte_carlo_terminal_distribution([0.01, 0.02]) is None


def test_historical_alignment_per_expression_real_or_suppressed():
    """Per-expression alignment is RECOMPUTED from each expression's OWN backtest
    evidence (so it genuinely differs by strategy — the bug where every
    expression of a view showed the same dial). Suppressed / below-MinTRL /
    no-design-score -> None ('not enough track record')."""
    from backend.view_markets import precompute

    def cfg(*, beat, ret=45.0, ca=80, dsr=0.5, dd=-12.0, verdict="unproven",
            n_obs=160, min_trl=100, dial="B"):
        return {"scores": {"construction_alignment": ca,
                           "backtest": {"pct_episodes_beat": beat,
                                        "deflated_sharpe": dsr,
                                        "total_return_pct": ret, "max_dd_pct": dd,
                                        "trust_verdict": verdict, "n_obs": n_obs,
                                        "min_trl": min_trl, "expression_dial": dial}}}

    # Real per-expression evidence -> a {score, letter}.
    a = precompute._historical_alignment(cfg(beat=75))
    assert a is not None and isinstance(a["score"], int) and a["letter"]

    # Different evidence -> a DIFFERENT score (the core of the fix).
    b = precompute._historical_alignment(cfg(beat=40, ret=10.0))
    assert b is not None and a["score"] != b["score"]

    # Suppressed dial -> None.
    supp = {"scores": {"construction_alignment": 80,
                       "backtest": {"expression_dial": "SUPPRESSED"}}}
    assert precompute._historical_alignment(supp) is None
    # Below MinTRL -> None.
    assert precompute._historical_alignment(cfg(beat=75, n_obs=10, min_trl=100)) is None
    # No design score / empty -> None.
    assert precompute._historical_alignment({}) is None


def test_round3_detail_fields_projected_from_precompute(client, db):
    """episodes / exit_period / historical_alignment / holdings position+weight /
    monte_carlo coerce from the cached precompute payload into the response."""
    v = _seed_curated_it(db)
    db.commit()
    fake = {
        "equity_curve": [],
        "holdings": [
            {"name": "REC", "symbol": "RECLTD", "return_pct": 12.3,
             "position": "long", "weight_pct": 20.0},
            {"name": "Nifty 50", "symbol": "^NSEI", "return_pct": -4.8,
             "position": "short", "weight_pct": None},
        ],
        "risk_return_ratio": 2.1,
        "underlying_symbol": None,
        "curve_basis": "in_position_episodes",
        "n_episodes": 8,
        "episode_boundaries": [1, 21],
        "episodes": [
            {"label": "TCS Q4FY22 print", "date": "Apr 2022",
             "return_pct": 5.1, "benchmark_pct": -1.2, "positive": True},
            {"label": "Q1FY23 margins", "date": "Jul 2022",
             "return_pct": -2.0, "benchmark_pct": 1.0, "positive": False},
        ],
        "positive_episodes": 1,
        "exit_period": "Held ~20 trading days (about a month) after each "
                       "weak-guidance print",
        "historical_alignment": {"score": 79, "letter": "B"},
        "monte_carlo": {
            "n_sims": 2000, "terminal_pct": [-5.0, 10.0, 50.0],
            "p05": -3.0, "p25": 12.0, "median": 30.0, "p75": 45.0,
            "p95": 70.0, "prob_loss": 0.08,
        },
    }
    with patch(
        "backend.routers.views.precompute.expression_precompute", return_value=fake,
    ):
        r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    cons = next(e for e in r.json()["expressions"] if e["tier"] == "conservative")
    assert cons["exit_period"].startswith("Held ~20 trading days")
    assert cons["historical_alignment"] == {"score": 79, "letter": "B"}
    assert cons["positive_episodes"] == 1
    assert len(cons["episodes"]) == 2
    assert cons["episodes"][0]["date"] == "Apr 2022"
    assert cons["episodes"][0]["positive"] is True
    # Holdings carry position + weight; the Nifty leg is the short side.
    short = next(h for h in cons["holdings"] if h["position"] == "short")
    assert short["name"] == "Nifty 50" and short["weight_pct"] is None
    long0 = next(h for h in cons["holdings"] if h["position"] == "long")
    assert long0["weight_pct"] == 20.0
    mc = cons["monte_carlo"]
    assert mc["n_sims"] == 2000 and mc["prob_loss"] == 0.08
    assert len(mc["terminal_pct"]) == 3


def test_headline_uses_average_per_occurrence_not_compounded(client, db):
    """The card/table/detail headline is the AVERAGE return over the event's past
    occurrences (from precompute) — NEVER the return compounded across all of them
    (the stored backtest total). A single deployment earns the average, not the
    stacked total."""
    from backend.view_markets import precompute

    v = _seed_curated_it(db)
    db.commit()

    exprs = db.query(ViewExpression).filter(ViewExpression.view_id == v.id).all()

    def _tier(e):
        return str(getattr(e.tier, "value", e.tier))

    cons_e = next(e for e in exprs if _tier(e) == "conservative")  # cfg total 48.83
    aggr_e = next(e for e in exprs if _tier(e) == "aggressive")    # cfg total 56.92

    def _avg(avg, bench, excess):
        return {
            **precompute._blank(),
            "avg_episode_return_pct": avg,
            "avg_episode_benchmark_pct": bench,
            "avg_episode_excess_pct": excess,
            "n_episodes": 8,
        }

    payloads = {
        str(cons_e.id): _avg(6.0, -0.5, 6.5),
        str(aggr_e.id): _avg(9.0, -0.5, 9.5),
    }

    def fake(expr_id):
        return payloads.get(str(expr_id), precompute._blank())

    with patch(
        "backend.routers.views.precompute.expression_precompute", side_effect=fake,
    ):
        r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    body = r.json()

    # Hero leads with the highest-AVERAGE expression (aggressive 9.0 > cons 6.0),
    # and the hero number is the AVERAGE, not the compounded 56.92.
    be = body["best_expression"]
    assert be["tier"] == "aggressive"
    assert be["total_return_pct"] == 9.0
    assert be["nifty_total_pct"] == -0.5
    assert be["n_episodes"] == 8

    # Conservative row: average 6.0, not the stored compounded 48.83.
    cons = next(e for e in body["expressions"] if e["tier"] == "conservative")
    assert cons["strategy_total_pct"] == 6.0
    assert cons["nifty_total_pct"] == -0.5
    assert cons["excess_return_pct"] == 6.5


def test_headline_falls_back_to_stored_total_when_no_precompute(client, db):
    """Uncached/dev (precompute blank) → fall back to the stored backtest fields so
    nothing breaks; only when precompute supplies an average do we override."""
    v = _seed_curated_it(db)
    db.commit()
    r = client.get(f"/api/views/{v.id}")  # ids not in the on-disk cache → blank
    assert r.status_code == 200, r.text
    cons = next(
        e for e in r.json()["expressions"] if e["tier"] == "conservative"
    )
    assert cons["strategy_total_pct"] == 48.83  # stored fallback, unchanged


def test_monte_carlo_horizon_models_single_occurrence(client, db):
    """The horizon kwarg bootstraps from the full sample but simulates a SHORTER
    path (one occurrence), so an upward-drifting series yields a smaller terminal
    spread than simulating the whole concatenated history."""
    from backend.services.backtest.validation.monte_carlo import (
        monte_carlo_terminal_distribution,
    )

    rets = [0.01, -0.005, 0.012, 0.003, -0.002, 0.008] * 8  # 48 obs, mild up-drift
    full = monte_carlo_terminal_distribution(rets, n_sims=400, seed=7)
    short = monte_carlo_terminal_distribution(
        rets, n_sims=400, seed=7, horizon=len(rets) // 4,
    )
    assert full is not None and short is not None
    # Same sample, shorter horizon → lower compounded terminal median.
    assert short["median"] < full["median"]
    # Backward compatible: no horizon == full sample horizon.
    same = monte_carlo_terminal_distribution(rets, n_sims=400, seed=7)
    assert same["median"] == full["median"]


def test_layman_transmission_and_expectation_labels(client, db):
    """Transmission edges get plain link labels; raw CAAR evidence never leaks."""
    v = _seed_view(db, title="Edge labels view")
    db.commit()
    r = client.get(f"/api/views/{v.id}")
    assert r.status_code == 200, r.text
    edge = r.json()["transmission"][0]
    assert edge["from_label"]
    assert edge["to_label"]
    assert edge["strength_label"] in {"weak link", "moderate link", "strong link"}
    # plain_evidence is built from edge_label only — never the raw stats string.
    if edge["plain_evidence"]:
        assert "t=" not in edge["plain_evidence"]
        assert "CAAR" not in edge["plain_evidence"]
