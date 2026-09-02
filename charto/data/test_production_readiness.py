"""Regression tests for Charto's production admission and recovery seams."""
from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request

import pytest

import dataserver as server


def test_default_service_tier_matches_the_deployment() -> None:
    assert server.LLM_SERVICE_TIER in {"default", "priority"}
    if "CHARTO_LLM_SERVICE_TIER" not in server.environ:
        assert server.LLM_SERVICE_TIER == "default"


def test_data_gate_rejects_without_overcommitting(monkeypatch) -> None:
    monkeypatch.setattr(server, "_DATA_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(server, "_DATA_STATE",
                        {"active": 0, "peak": 0, "rejected": 0,
                         "completed": 0})
    assert server._data_slot_acquire(timeout=0)
    assert not server._data_slot_acquire(timeout=0)
    assert server._data_state()["active"] == 1
    assert server._data_state()["rejected"] == 1
    server._data_slot_release()
    assert server._data_state()["active"] == 0


def test_chat_gate_queues_a_second_wave(monkeypatch) -> None:
    monkeypatch.setattr(server, "_CHAT_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(server, "_CHAT_STATE",
                        {"active": 0, "peak": 0, "rejected": 0,
                         "completed": 0})
    monkeypatch.setattr(server, "_CHAT_QUEUE_TIMEOUT_S", 0.2)
    assert server._chat_slot_acquire()

    acquired = []
    waiter = threading.Thread(target=lambda: acquired.append(
        server._chat_slot_acquire()))
    waiter.start()
    server.time.sleep(0.02)
    server._chat_slot_release()
    waiter.join(timeout=1)

    assert acquired == [True]
    assert server._chat_state()["peak"] == 1
    server._chat_slot_release()


def test_tool_overload_is_explicit_and_retryable(monkeypatch) -> None:
    monkeypatch.setattr(server, "_data_slot_acquire", lambda *_a, **_k: False)
    out = server._run_tool("probe", lambda: {"ok": True}, {})
    assert out["retryable"] is True
    assert out["retry_after_s"] >= 1
    assert "busy" in out["error"].lower()


def _http_error(code: int, retry_after: str = "0",
                retry_after_ms: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after}
    if retry_after_ms is not None:
        headers["retry-after-ms"] = retry_after_ms
    return urllib.error.HTTPError(
        "https://example.invalid", code, "failed",
        headers, io.BytesIO(b"failed"))


def test_azure_retry_replays_only_a_pre_response_transient(monkeypatch) -> None:
    calls = []
    sentinel = object()

    def open_once(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429)
        return sentinel

    monkeypatch.setattr(urllib.request, "urlopen", open_once)
    monkeypatch.setattr(server.time, "sleep", lambda _s: None)
    req = urllib.request.Request("https://example.invalid")
    assert server._urlopen_with_retry(
        req, timeout=1, context=None, attempts=3) is sentinel
    assert len(calls) == 2


def test_azure_retry_does_not_replay_a_deterministic_client_error(monkeypatch) -> None:
    calls = []

    def always_bad(*_args, **_kwargs):
        calls.append(1)
        raise _http_error(400)

    monkeypatch.setattr(urllib.request, "urlopen", always_bad)
    req = urllib.request.Request("https://example.invalid")
    with pytest.raises(urllib.error.HTTPError, match="HTTP Error 400"):
        server._urlopen_with_retry(req, timeout=1, context=None, attempts=3)
    assert len(calls) == 1


def test_azure_retry_honours_millisecond_header() -> None:
    assert server._retry_after_seconds(
        _http_error(429, retry_after="", retry_after_ms="750"), 1) == 0.75


def test_backup_health_distinguishes_fresh_from_stale(tmp_path, monkeypatch) -> None:
    marker = tmp_path / "last.json"
    marker.write_text(json.dumps({"completed_at_epoch": 1_000,
                                  "blob": "backup/users/a.db.gz"}))
    monkeypatch.setattr(server, "_BACKUP_MARKER", marker)
    monkeypatch.setattr(server, "_BACKUP_MAX_AGE_S", 7_200)
    monkeypatch.setattr(server, "_REQUIRE_FRESH_BACKUP", True)
    fresh = server._backup_health(now=2_000)
    assert fresh["ok"] and fresh["fresh"]
    stale = server._backup_health(now=10_000)
    assert not stale["ok"] and not stale["fresh"]
    assert stale["age_s"] == 9_000


def test_health_route_is_not_classified_as_heavy_data() -> None:
    assert not server._is_heavy_http_path("/health")
    assert server._is_heavy_http_path("/indicator")
    assert server._is_heavy_http_path("/api/markets/ohlc")
    assert server._is_heavy_http_path("/replay")
    assert server._is_heavy_http_path("/live")


def test_deep_health_makes_execution_part_of_readiness(monkeypatch) -> None:
    monkeypatch.setattr(server, "_backup_health", lambda: {"ok": True})
    monkeypatch.setattr(server, "AZURE_ENDPOINT", "https://example.invalid")
    monkeypatch.setattr(server, "AZURE_KEY", "secret")
    monkeypatch.setattr(server, "_REQUIRE_EXECUTION", False)
    monkeypatch.setattr(server.execution_bridge, "available",
                        lambda: (False, "execution engine unavailable"))

    code, payload = server._health_report(deep=True)

    assert code == 503
    assert payload["ready"] is False
    assert payload["checks"]["execution"]["checked"] is True
    assert payload["checks"]["execution"]["ok"] is False


# ── model fallback ─────────────────────────────────────────────────────────
#
# A deployment outage does not arrive as an HTTP error on the streaming path.
# Measured 2026-08-31: Azure accepted the connection, returned 200, and sent
# the fault in band as an SSE `error` event — so the pre-response retry never
# engaged and the turn died with a healthy sibling deployment available on the
# same endpoint. These pin the seam that now catches it.


def test_stream_error_is_read_from_where_the_api_actually_puts_it() -> None:
    # `ev["message"]` is what the code used to read, and the API has never set
    # it: the whole point of this parse is the nesting.
    assert server._stream_error_parts(
        {"type": "error",
         "error": {"type": "server_error", "message": "no healthy upstream"}},
    ) == ("server_error", "no healthy upstream")
    # `response.failed` nests it one level deeper again.
    assert server._stream_error_parts(
        {"type": "response.failed",
         "response": {"error": {"code": "rate_limit", "message": "slow down"}}},
    ) == ("rate_limit", "slow down")
    assert server._stream_error_parts({"type": "error"}) == ("", "")


def test_only_a_dead_deployment_demotes() -> None:
    """A bad request is not an outage. Retrying a 400 on a second model only
    asks it the same wrong question, and a 429 is capacity on a deployment
    that IS serving — `_urlopen_with_retry` backs off for that already."""
    assert server._llm_unhealthy(503, "")
    assert server._llm_unhealthy(502, "")
    assert server._llm_unhealthy(504, "")
    assert server._llm_unhealthy(None, "no healthy upstream")
    assert not server._llm_unhealthy(400, "invalid tool schema")
    assert not server._llm_unhealthy(429, "rate limit exceeded")
    assert not server._llm_unhealthy(401, "access denied")


def test_demotion_expires_and_stops_retrying_the_dead_arm(monkeypatch) -> None:
    monkeypatch.setattr(server, "LLM_DEPLOYMENT", "primary")
    monkeypatch.setattr(server, "LLM_FALLBACK", "backup")
    monkeypatch.setattr(server, "_llm_demoted_until", 0.0)

    assert server._model() == "primary"
    assert server._stream_models() == ["primary", "backup"]

    assert server._demote("no healthy upstream") == "backup"
    assert server._model() == "backup"
    # One attempt while demoted: a second call to the arm that just failed is
    # not a fallback, it is a 503 charged to every turn of the outage.
    assert server._stream_models() == ["backup"]

    monkeypatch.setattr(server, "_llm_demoted_until",
                        server.time.monotonic() - 1)
    assert server._model() == "primary"


def test_no_fallback_configured_fails_loudly(monkeypatch) -> None:
    monkeypatch.setattr(server, "LLM_DEPLOYMENT", "primary")
    monkeypatch.setattr(server, "LLM_FALLBACK", "")
    monkeypatch.setattr(server, "_llm_demoted_until", 0.0)
    assert server._stream_models() == ["primary"]
    assert server._demote("down") == ""
    assert server._model() == "primary"


# ── the shared connection, and the resample cache ───────────────────────────
#
# Both of these exist because of what a 2026-08-31 load run on the production
# box measured, and both are the kind of fix that a later refactor could undo
# without any visible symptom until concurrency arrives. So they are pinned.


def test_bar_store_hands_each_thread_its_own_connection() -> None:
    """The fault: one sqlite3 handle shared by every request thread.

    Measured in production at 1-3 failures per 120 requests once 20-40 chart
    opens overlap, raising `InterfaceError: bad parameter or other API misuse`
    from `_symbol_ready` — on `do_POST`, so a chat turn died rather than a
    chart. A control run against the pre-fix design failed 36 of 48 threads,
    including one `ValueError` from a torn cursor feeding a short row into the
    resampler, which is worse than a 500 because it is not an error at all.
    """
    import threading

    N = 16
    # Every thread is held here until all N have a connection, and that barrier
    # is load-bearing rather than tidy. CPython RECYCLES both of the identities
    # this test compares: `get_ident()` is reused once a thread exits, and a
    # connection's `id()` is its address, reusable as soon as the thread-local
    # holding it is collected. Let the early threads finish and the later ones
    # inherit their identities — measured, this asserted 14 distinct threads out
    # of 16 while the code under test was perfectly correct. Keeping all N alive
    # simultaneously is what makes "distinct" mean distinct.
    ready = threading.Barrier(N + 1, timeout=30)
    seen: list[int | None] = [None] * N
    errors: list[str] = []

    def touch(slot: int) -> None:
        try:
            for _ in range(6):
                server._con.execute("SELECT 1").fetchone()
            seen[slot] = id(server._con._c())
        except Exception as exc:                      # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            # Reached on the error path too: a thread that dies before the
            # barrier would hang the other fifteen on a timeout, turning one
            # clear assertion failure into a 30-second stall with no reason.
            try:
                ready.wait()
            except threading.BrokenBarrierError:
                pass

    threads = [threading.Thread(target=touch, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    ready.wait()          # all N alive, all N connections still referenced
    for t in threads:
        t.join()

    assert not errors, errors
    # Distinct connections, one per thread — the property that makes the race
    # impossible rather than merely unlikely.
    assert len(set(seen)) == N and None not in seen

    # And the private cache is bounded absolutely, not just divided: the live
    # connection count follows traffic, so a share of the budget is not a cap.
    assert server._CACHE_KIB_EACH <= server._DB_CACHE_MAX_KIB


def test_resample_cache_is_invisible_and_bounded() -> None:
    """A cache that changes an answer is a bug with better latency.

    `_resample_intraday` costs one iteration per MINUTE while the chart asks in
    BARS, so an hourly open folds 180,400 rows in pure Python and holds the GIL
    for all of it. Every indicator repeats it on the identical series.
    """
    key_a = ("SYNTH", 5, None, 20400, 4000)
    key_b = ("SYNTH", 60, None, 180400, 3000)
    bars_a = [[1, 2.0, 3.0, 1.0, 2.5, 10]]
    bars_b = [[2, 3.0, 4.0, 2.0, 3.5, 20]]

    server._intra_cache.clear()
    assert server._intra_cached(key_a) is None

    server._intra_store(key_a, bars_a, True)
    server._intra_store(key_b, bars_b, False)
    assert server._intra_cached(key_a) == (bars_a, True)
    # Interval and window are part of the identity: an hourly answer must never
    # be served to a five-minute request, nor a pan to a fresh open.
    assert server._intra_cached(key_b) == (bars_b, False)
    assert server._intra_cached(("SYNTH", 5, 999, 20400, 4000)) is None

    for i in range(server._INTRA_CACHE_MAX + 25):
        server._intra_store(("FILL", 5, None, i, 4000), [[i]], False)
    assert len(server._intra_cache) <= server._INTRA_CACHE_MAX

    server._intra_store(key_a, bars_a, True)
    server._intra_drop("SYNTH")
    assert server._intra_cached(key_a) is None
    server._intra_cache.clear()


def test_live_bar_never_reads_from_the_cache(monkeypatch) -> None:
    """A forming bar is merged INTO rows before the resample.

    So the output is not a function of the cache key, and the live path has to
    take the long way every time. Getting this wrong would freeze the newest
    candle at whatever it looked like on first paint.
    """
    import inspect

    src = inspect.getsource(server.get_bars)
    assert "live_bar = form is not None" in src
    assert "key = None if live_bar else" in src
    # and the merge happens only on that same branch, never on a cached read
    assert src.index("if live_bar:") > src.index("key = None if live_bar else")


# ── the grading and composition rules, 2026-09-01 ────────────────────────────
#
# Three behaviours that a later refactor could undo with nothing visible going
# wrong: a level graded from the wrong evidence still prints a confident word,
# a cap whose default drifts still draws SOMETHING, and a panel that renders
# the full sweep every time still renders. All three failed exactly that way
# once already, which is why they are pinned rather than trusted.


def test_level_strength_is_graded_on_evidence_not_touch_count() -> None:
    """The rule that used to be `touches >= 4 -> strong`.

    On one real chart that called a level STRONG which had broken four of its
    five re-tests, while calling one that turned price away four times out of
    five weak — for having been found once too few. The grade has to move with
    what happened when price came back, so these cases pin the ladder itself.
    """
    grade = lambda h, b, since=10: server._grade_level(  # noqa: E731
        {"held": h, "broke": b}, h + b + 1, since, 300)[0]

    assert grade(4, 1) == "strong"        # the case that reported weak before
    assert grade(3, 0) == "strong"
    # Two data points are not a track record, whatever the ratio says. This is
    # the sample gate, and it is the reason "1 of 1" cannot read as perfect.
    assert grade(2, 0) == "moderate"
    assert grade(1, 0) == "moderate"
    # A coin flip is not support. The old rule graded this one STRONG.
    assert grade(2, 2) == "weak"
    assert grade(0, 3) == "weak"
    # No graded re-test is no evidence, and no evidence is weak — not unrated,
    # because a fourth word on the chart would be a second vocabulary.
    assert grade(0, 0) == "weak"
    # Recency decays a good record without erasing it.
    assert grade(4, 1, since=250) == "moderate"


def test_levels_draw_two_a_side_and_park_the_rest(monkeypatch) -> None:
    """The cap, and the words on the chart.

    Both regressed together once: `max_draw` changed meaning from "levels
    overall" to "levels per side" and its default stayed 3, so every side drew
    three against a documented cap of two — and nothing failed, because
    drawing too much still draws.
    """
    # Pin the symbol. `_sym()` reads a THREAD-LOCAL that do_GET/do_POST stamp
    # per request, and another test module in the same run leaves its own
    # symbol on this thread — under which the scan finds no bars, no levels,
    # and every assertion below passes vacuously. Passing alone and failing in
    # the suite is the tell, and a vacuous pass is worse than either.
    monkeypatch.setattr(server._req, "symbol", "RELIANCE", raising=False)
    emitted: list = []
    monkeypatch.setattr(server, "_scene_add", emitted.append)
    server.tool_get_levels(interval="15m", lookback_bars=300, draw=True)
    assert emitted, "no levels detected — the assertions below would be vacuous"

    drawn = [a for a in emitted if not a.get("hidden")]
    per_side: dict = {}
    for a in drawn:
        per_side[a["role"]] = per_side.get(a["role"], 0) + 1
    assert per_side and all(n <= 2 for n in per_side.values()), per_side

    # Nothing above the cut is discarded — it is parked with its eye off, so
    # "show me the others" costs a click and not a second scan.
    assert all(a.get("hidden") for a in emitted if a not in drawn)

    for a in emitted:
        # The chart carries the CONCLUSION. The arithmetic behind it belongs on
        # the Layers row, where a number you have to think about can be read.
        assert "held" not in a["label"], a["label"]
        assert a["label"].split(" · ")[-1] in ("Strong", "Moderate", "Weak")
        assert a["detail"], "the Layers row would have nothing to explain"


def test_panel_density_follows_the_chart_not_the_wording() -> None:
    """A three-word question used to be answered with thirteen hero tiles.

    The fix must not become a keyword table: nothing here may consult the
    user's phrasing, or the first paraphrase puts the gallery back. So the
    default comes from whether the sweep MARKED anything, and the model's own
    declared `detail` overrides it in both directions.
    """
    marked = {"chart_drawn": 3, "candles_marked": 0}
    bare = {"chart_drawn": 0, "candles_marked": 0}

    assert server._panel_density(bare, None) == "brief"
    assert server._panel_density(marked, None) == "full"
    # The model asked for an inventory because the user did; chart state does
    # not get to overrule that, in either direction.
    assert server._panel_density(bare, "full") == "full"
    assert server._panel_density(marked, "brief") == "brief"
    # An unknown value is not a third mode — it falls back to the chart.
    assert server._panel_density(marked, "enormous") == "full"


# ── the payload is the answer: five facts the model must never have to derive ─
#
# Every wrong number in the 02 Sep research-mode session traced to the same
# shape of bug — a fact this file already knew, handed over in a form that
# made the model work it out. None of them was the model inventing anything.
# So each one is pinned here as a property OF THE PAYLOAD, which is the layer
# that can actually be tested.

def test_explain_move_keeps_the_windows_last_session(monkeypatch) -> None:
    """The newest bar is what a causal question is usually about.

    The session list used to be `range(i0, min(i1, i0 + 9) + 1)` — the FIRST
    ten of the window — so an eleven-session window handed over every day but
    the newest. Asked what drove the last big down day, the model named the
    second-biggest fall and was right about everything it could see.
    """
    monkeypatch.setattr(server._req, "symbol", "RELIANCE", raising=False)
    out = server.tool_explain_move(frm="2026-06-01", to="2026-07-22")
    assert not out.get("error"), out.get("error")
    sess = out["sessions"]
    assert sess, "no sessions — the assertions below would be vacuous"
    assert sess[-1]["date"] == out["window"]["to"], (
        "the window's last session is missing from the rows the model reads")
    # and the note under it only tells the truth if the hole is in the middle
    if out.get("sessions_omitted"):
        assert sess[0]["date"] == out["window"]["from"]


def test_compare_ranks_the_peers_instead_of_leaving_it_to_the_model(monkeypatch) -> None:
    """Ordering signed returns is arithmetic, so it happens in Python.

    `metrics` is keyed by symbol and carries no order. Asked to rank eight
    peers the model reported that a −13.04% subject "outperformed only" three
    names at −10.35, −10.10 and −12.36 — the three it had just said it
    finished behind. Comparing negatives is exactly where this goes wrong.
    """
    monkeypatch.setattr(server._req, "symbol", "RELIANCE", raising=False)
    out = server.tool_compare_symbols(
        symbols=["RELIANCE", "BPCL", "IOC", "HINDPETRO", "MRPL"],
        interval="1d", lookback_bars=130)
    assert not out.get("error"), out.get("error")
    rank = out["ranking"]
    rets = [r["return_pct"] for r in rank]
    assert rets == sorted(rets, reverse=True), rank
    assert [r["rank"] for r in rank] == list(range(1, len(rank) + 1))
    sr, m = out["subject_rank"], out["metrics"]
    mine = m[sr["symbol"]]["return_pct"]
    # the two lists are the whole point: a name may appear in exactly one
    assert set(sr["beat"]) & set(sr["lost_to"]) == set()
    assert all(m[k]["return_pct"] < mine for k in sr["beat"]), sr
    assert all(m[k]["return_pct"] > mine for k in sr["lost_to"]), sr
    assert sr["rank"] == 1 + len(sr["lost_to"])


def test_trendlines_carry_a_direction_word(monkeypatch) -> None:
    """The card said "Rising resistance"; the reply said "descending".

    `_trendline_name` computed the word for the panel and the model got only
    `slope_per_bar`. Resistance colloquially reads as a falling line, so it
    guessed, and the reply lost to its own widget.
    """
    monkeypatch.setattr(server._req, "symbol", "RELIANCE", raising=False)
    out = server.tool_get_trend(interval="1d", lookback_bars=1500)
    lines = out.get("trendlines") or out.get("drawn_trendlines") or []
    assert lines, "no trendlines fitted — the assertions below would be vacuous"
    for x in lines:
        assert x.get("direction") in ("rising", "descending", "flat"), x
        slope = x.get("slope_per_bar")
        if slope:
            assert x["direction"] == ("rising" if slope > 0 else "descending"), x


def test_results_aggregates_declare_their_sample(monkeypatch) -> None:
    """`recent` is a window onto the sample, never the sample.

    Every average is over all measured results; `recent` is the six newest.
    The model printed four rows and quoted the full-sample averages under
    them, so two of three headline numbers could not be reconciled with the
    table they sat on.
    """
    monkeypatch.setattr(server._req, "symbol", "RELIANCE", raising=False)
    out = server.tool_evaluate_results(horizon_bars=5)
    assert not out.get("error"), out.get("error")
    assert out["aggregate_sample_n"] == out["events_evaluated"]
    assert out["recent_shown"] == len(out["recent"])
    assert out["_sample_note"], "nothing tells the model the samples differ"
    # the bug only bites when they DIFFER, which is the ordinary case
    if out["recent_shown"] != out["aggregate_sample_n"]:
        assert str(out["aggregate_sample_n"]) in out["_sample_note"]


def test_hand_picked_levels_are_checked_against_the_nearest(monkeypatch) -> None:
    """`draw_ids` skipped the ranker, the cap and proximity, unchecked.

    Asked for "the levels that actually matter" the model drew a Strong
    support 12.6% below spot, parked the Strong support 0.49% below it, left
    its second support slot empty, and then explained the omission as keeping
    the chart readable — a reason the tool never gave it.
    """
    monkeypatch.setattr(server._req, "symbol", "RELIANCE", raising=False)
    monkeypatch.setattr(server, "_scene_add", lambda *a, **k: None)
    base = server.tool_get_levels(interval="1d", lookback_bars=1500)
    lv = base.get("levels") or []
    sup = [x for x in lv if x["role"] == "support"]
    res = [x for x in lv if x["role"] == "resistance"]
    assert len(sup) >= 2 and res, "not enough levels — assertions would be vacuous"
    near = min(sup, key=lambda z: abs(z["distance_pct"]))
    far = max(sup, key=lambda z: abs(z["distance_pct"]))

    skipped = server.tool_get_levels(
        interval="1d", lookback_bars=1500, draw=True,
        draw_ids=[min(res, key=lambda z: abs(z["distance_pct"]))["id"], far["id"]])
    note = skipped.get("_drawn_note", "")
    assert "WARNING" in note and near["id"] in note, note
    assert "readab" in note, "the invented rationale is not ruled out"

    # and a set that DOES include the nearest each side raises nothing
    ok = server.tool_get_levels(
        interval="1d", lookback_bars=1500, draw=True,
        draw_ids=[min(res, key=lambda z: abs(z["distance_pct"]))["id"], near["id"]])
    assert "WARNING" not in ok.get("_drawn_note", "")
