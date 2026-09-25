"""read_annual_report: the pages of the report that answer the model's question."""
from backend.services import annual_report as ar

PAGES = {
    1: "Petronet LNG Limited annual report cover",
    7: "Dahej terminal capacity utilisation was 99% during the year",
    8: "Board of directors and governance",
    9: "Kochi terminal capacity utilisation remained low at 21%",
}


def test_pages_are_ranked_by_the_question_words():
    assert ar._rank(PAGES, "capacity utilisation at Dahej", 2) == [7, 9]
    assert ar._rank(PAGES, "the of and", 3) == []    # nothing to look for


def test_read_returns_pages_url_and_page_citation(monkeypatch):
    monkeypatch.setattr(ar, "_find_doc", lambda s, y: {
        "sha256": "x", "title": "Petronet LNG AR 2024-2025", "period": "2024-2025",
        "url": "https://nsearchives.nseindia.com/ar.pdf", "pages": 4, "blob_text": "b"})
    monkeypatch.setattr(ar, "_pages", lambda sha, path: PAGES)
    monkeypatch.setattr(ar, "available_periods", lambda s: ["2024-2025", "2023-2024"])
    out = ar.read("petronet", "Dahej capacity utilisation")
    assert [p["page"] for p in out["pages"]][:2] == [7, 9]
    assert out["cite_as"].endswith("(https://nsearchives.nseindia.com/ar.pdf#page=N))")
    assert out["other_years"] == ["2023-2024"]
    assert [p["page"] for p in ar.read("PETRONET", pages=[8, 99])["pages"]] == [8]


def test_ligature_glyphs_are_unfolded():
    assert "construcƟon".translate(ar._LIGATURES) == "construction"
