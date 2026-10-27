from backend.routers import stock_detail


def test_company_research_batches_page_derivations(monkeypatch):
    monkeypatch.setattr(
        stock_detail,
        "get_quarters",
        lambda symbol, basis, limit, authorization: {
            "symbol": symbol, "basis": basis, "limit": limit,
            "internal": authorization is stock_detail._INTERNAL_TOOL_AUTH,
        },
    )
    monkeypatch.setattr(
        stock_detail,
        "get_shareholding",
        lambda symbol, authorization: {
            "symbol": symbol,
            "internal": authorization is stock_detail._INTERNAL_TOOL_AUTH,
        },
    )

    result = stock_detail.get_company_research_data(
        "reliance", ["quarters", "shareholding"], basis="standalone",
    )

    assert result["available"] is True
    assert result["errors"] == {}
    assert result["sections"]["quarters"] == {
        "symbol": "RELIANCE", "basis": "standalone", "limit": 12, "internal": True,
    }
    assert result["sections"]["shareholding"]["internal"] is True


def test_company_research_rejects_unknown_sections():
    result = stock_detail.get_company_research_data("TCS", ["secret_table"])
    assert result["available"] is False
    assert "unknown sections" in result["error"]


def test_company_research_caps_broad_annual_report(monkeypatch):
    tasks = [
        {"task": f"topic_{i}", "groups": [{"grp": "g", "facts": list(range(80))}]}
        for i in range(3)
    ]
    monkeypatch.setattr(
        stock_detail,
        "get_annual_report",
        lambda symbol, authorization: {
            "symbol": symbol, "documents": list(range(20)), "tasks": tasks,
        },
    )

    result = stock_detail.get_company_research_data("TCS", ["annual_report"])
    report = result["sections"]["annual_report"]
    count = sum(
        len(group["facts"])
        for task in report["tasks"]
        for group in task["groups"]
    )
    assert count == 120
    assert len(report["documents"]) == 8
    assert report["chat_truncated"] is True
