"""A screen reaches the user as a card, with its true match count."""
from backend.services.fundamentals_screen import _apply_exclude, screen_card_columns


def _data():
    return {"results": [
        {"symbol": "PCJEWELLER", "name": "PC Jeweller", "market_cap_cr": 8652,
         "market_cap": 8652, "revenue_growth": 270.76, "roe": 11.2, "one_year_pct": -26.3},
    ], "sorted_by": {"field": "revenue_growth", "dir": "desc"},
       "count": 1, "total_matched": 468}


def test_columns_lead_with_market_cap_then_the_ranked_metric_with_units():
    cols = screen_card_columns(_data())
    assert [c["key"] for c in cols] == ["market_cap_cr", "revenue_growth", "roe", "one_year_pct"]
    assert {c["key"]: c["unit"] for c in cols} == {
        "market_cap_cr": "cr", "revenue_growth": "pct_signed",
        "roe": "pct", "one_year_pct": "pct_signed"}


def test_growth_horizon_is_named_in_the_label():
    d = {**_data(), "growth_years": 5}
    assert screen_card_columns(d)[1]["label"] == "Revenue Growth (5y CAGR)"


def test_no_rows_no_columns():
    assert screen_card_columns({"results": []}) == []


def test_exclusions_come_off_the_true_total_too():
    out = _apply_exclude(_data(), ["PCJEWELLER"])
    assert out["count"] == 0 and out["total_matched"] == 467


def test_a_long_result_is_trimmed_by_rows_and_stays_valid_json():
    """A [:6000] cut left the model 39 of 100 rows in broken JSON, with the
    card hint (at the tail) gone and no sign anything was missing."""
    import json
    from backend.services.tool_registry import _fit_for_llm
    rows = [{"symbol": f"S{i}", "name": "x" * 40, "revenue_growth": i} for i in range(100)]
    data = {"_render_hint": "screen_results_card", "count": 100,
            "total_matched": 468, "results": rows}
    s = _fit_for_llm(data, 3000)
    got = json.loads(s)
    assert len(s) <= 3000 and got["_render_hint"] == "screen_results_card"
    assert got["total_matched"] == 468
    n = len(got["results"])
    assert 0 < n < 100 and got["_trimmed"].startswith(f"{n} of 100 results")
    assert "card shows all" in got["_trimmed"]


def test_short_result_is_untouched():
    from backend.services.tool_registry import _fit_for_llm
    assert _fit_for_llm({"a": [1, 2]}, 6000) == '{"a": [1, 2]}'


def test_verified_names_replace_truncated_ones_and_failures_keep_rows(monkeypatch):
    from backend.services import fundamentals_screen as fs

    class Boom:
        def __enter__(self):
            raise RuntimeError("db down")
        def __exit__(self, *a):
            return False
    import backend.database as dbmod
    monkeypatch.setattr(dbmod, "SessionLocal", lambda: Boom())
    data = {"results": [{"symbol": "VENTIVE", "name": "Ventive Hospita"}]}
    assert fs.with_verified_names(data) == data
