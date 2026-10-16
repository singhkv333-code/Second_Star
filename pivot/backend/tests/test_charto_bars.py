"""The chart-store reader trims each range to the right window."""
from unittest.mock import patch

from backend.market import charto_bars as cb

IST = 19800


def _bars(start: int, n: int, step: int) -> list[dict]:
    return [{"t": start + i * step, "c": 100.0 + i} for i in range(n)]


def test_1d_keeps_only_the_newest_ist_session() -> None:
    day = 1_792_000_000 // 86400 * 86400 - IST          # an IST midnight
    yesterday = _bars(day - 86400 + 4 * 3600, 6, 300)
    today = _bars(day + 4 * 3600, 5, 300)
    with patch.object(cb, "bars", return_value=yesterday + today):
        got = cb.series("RELIANCE", "1D")
    assert [c for _, c in got] == [c["c"] for c in today]


def test_1m_trims_to_31_days_back_from_the_newest_bar() -> None:
    rows = _bars(1_780_000_000, 40, 86400)
    with patch.object(cb, "bars", return_value=rows):
        got = cb.series("RELIANCE", "1M")
    assert got[-1][0] == rows[-1]["t"]
    assert got[0][0] >= rows[-1]["t"] - 31 * 86400
    assert len(got) == 32


def test_empty_store_is_an_empty_series() -> None:
    with patch.object(cb, "bars", return_value=[]):
        assert cb.series("NOPE", "1Y") == []
        assert cb.session_closes(["NOPE"]) == {}
