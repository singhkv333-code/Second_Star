"""Footprint: venue side conventions, the live recorder, and the per-bar fold."""
import footprint as fp


def test_aggressor_follows_each_venues_convention():
    # Bybit reports the taker; Coinbase reports the MAKER, so it is flipped.
    assert fp.aggressor("bybit", "Buy") == "buy"
    assert fp.aggressor("bybit", "Sell") == "sell"
    assert fp.aggressor("coinbase", "buy") == "sell"
    assert fp.aggressor("coinbase", "sell") == "buy"
    assert fp.aggressor("bybit", "") is None


def test_nice_step_is_a_1_2_5_number_at_or_below():
    assert fp.nice_step(1.66) == 1
    assert fp.nice_step(0.049) == 0.02
    assert fp.nice_step(7) == 5


def test_live_minutes_are_served_while_forming_and_kept_when_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "_con", None)
    monkeypatch.setattr(fp, "_open", {})
    monkeypatch.setattr(fp, "_steps", {})
    fp.bind(tmp_path / "footprint.db")
    fp.record("BTCUSDT", 600, 100.0, 2.0, "bybit", "Buy")
    fp.record("BTCUSDT", 610, 100.4, 1.0, "bybit", "Sell")
    assert sum(r[2] for r in fp.minutes("BTCUSDT", 600, 660)) == 2.0   # forming, from memory
    fp.record("BTCUSDT", 665, 101.0, 0.5, "bybit", "Buy")              # closes minute 600
    stored = fp._con.execute("SELECT SUM(buy), SUM(sell) FROM fp WHERE ts=600").fetchone()
    assert stored == (2.0, 1.0)
    fp.record("BTCUSDT", 630, 100.0, 1.0, "bybit", "Buy")              # late print, added
    assert fp._con.execute("SELECT SUM(buy) FROM fp WHERE ts=600").fetchone()[0] == 3.0
    monkeypatch.setattr(fp, "_con", None)


def _bar(t=0, o=100, h=104, l=100, c=103, v=None):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_fold_totals_delta_poc_and_value_area():
    # rows at 100..103 (row=1): (buy, sell)
    cells = [(0, 100.0, 1, 4, 1), (0, 101.0, 2, 2, 1), (0, 102.0, 9, 1, 1), (60, 103.0, 1, 0, 1)]
    [b] = fp.fold([_bar(v=21)], cells, lambda m: 0, row=1.0)
    assert (b["buy"], b["sell"], b["delta"]) == (13, 7, 6)
    assert b["poc"] == 102.0
    assert b["val"] <= 102.0 < b["vah"]
    assert [r[0] for r in b["rows"]] == [103.0, 102.0, 101.0, 100.0]      # top first
    assert b["covered"] == round(20 / 21, 3)


def test_diagonal_imbalance_compares_buys_with_the_sells_one_row_below():
    # buy at 102 = 9 vs sell at 101 = 2 → 4.5x → buy imbalance
    # sell at 100 = 4 vs buy at 101 = 2 → 2x  → not at 3x
    cells = [(0, 100.0, 1, 4, 1), (0, 101.0, 2, 2, 1), (0, 102.0, 9, 1, 1)]
    [b] = fp.fold([_bar()], cells, lambda m: 0, row=1.0, ratio=3.0)
    imb = {r[0]: r[3] for r in b["rows"]}
    assert imb[102.0] == "buy"
    assert imb[100.0] == ""


def test_three_stacked_imbalances_make_a_zone():
    cells = [(0, 100.0, 0, 1, 1), (0, 101.0, 5, 1, 1), (0, 102.0, 5, 1, 1),
             (0, 103.0, 5, 1, 1), (0, 104.0, 1, 1, 1)]
    [b] = fp.fold([_bar(h=105)], cells, lambda m: 0, row=1.0, ratio=3.0, stack=3)
    assert b["zones"] == [{"side": "buy", "lo": 101.0, "hi": 104.0}]


def test_a_bar_with_no_tape_says_so():
    [b] = fp.fold([_bar(v=5)], [], lambda m: 0, row=1.0)
    assert b["rows"] == [] and b["covered"] == 0.0
