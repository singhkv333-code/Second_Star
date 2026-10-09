"""Pivot is the one paywall: with CHARTO_PAYWALL unset, charto's own caps are
lifted while the AI-credit ledger Pivot's chat meters through stays on."""
import threading
import sqlite3

import entitlements as ent


def _bound(gates: bool, monkeypatch):
    monkeypatch.setattr(ent, "CHARTO_GATES", gates)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    ent.bind(con, threading.Lock())


def test_charto_caps_are_lifted_but_credits_stay_metered(monkeypatch):
    _bound(False, monkeypatch)
    assert ent.value(None, "chart.panes") is None
    assert ent.value(None, "chart.history_bars") is None
    assert ent.value(None, "alerts.watchlist") is True
    assert ent.value(None, "ai.summaries")["limit"] is None
    ent.check_count(None, "chart.panes", 99)            # no refusal
    ent.require_flag(None, "chart.custom_timeframes")   # no refusal
    assert ent.value(None, "ai.credits")["limit"] == ent.CATALOG[
        "features"]["ai.credits"]["values"]["anonymous"]


def test_switching_charto_gates_on_restores_the_caps(monkeypatch):
    _bound(True, monkeypatch)
    assert ent.value(None, "chart.panes") == ent.CATALOG[
        "features"]["chart.panes"]["values"]["anonymous"]
