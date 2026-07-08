"""Unit tests for the GAN R4 thematic-scenario + onboarding detectors.

These lock in the deterministic routing layer that converts the
thematic-thesis and vague classes from a bare-ask_user/prose-only punt
into a decode-and-propose path. Pure functions, no I/O.
"""
import pytest

from backend.services.thematic_map import (
    basket_weights,
    detect_thematic_scenario,
    extract_capital_inr,
    is_scared_idle_cash,
    is_unrealistic_return,
    is_vague_onboarding,
    winners_losers_block,
)


@pytest.mark.parametrize("text,key", [
    ("If India-Pakistan tensions blow up into a real shooting war, "
     "what's the trade? Build it for me.", "conflict_war"),
    ("rupee lagatar gir raha hai, kaunse stocks jeetenge? kuch banao "
     "mere liye, around 75k", "inr_depreciation"),
    ("IMD is hinting at a below-normal monsoon this season. Position my "
     "portfolio for it — I can put in about 1 lakh.", "monsoon_drought"),
    ("Give me a strategy that rides the RBI rate-cut cycle.", "rate_cut"),
    ("Crude just spiked 15% — hedge my portfolio against it staying high",
     "crude_spike"),
    ("position me for a severe El Nino drought hitting Indian agriculture",
     "monsoon_drought"),
    ("strategy that profits from an economic slowdown", "slowdown"),
])
def test_thematic_detected(text, key):
    s = detect_thematic_scenario(text)
    assert s is not None, text
    assert s.key == key


@pytest.mark.parametrize("text", [
    "what is the rupee at",
    "what's nifty trading at",
    "analyse HDFC bank",
    "build me an agent that buys INFY every monday",
    "show me the option chain for nifty",
    "",
    "hi",
])
def test_thematic_not_detected(text):
    assert detect_thematic_scenario(text) is None


def test_thematic_basket_has_real_names_both_sides():
    s = detect_thematic_scenario("strategy that profits from a war")
    assert s is not None
    assert len(s.winners) >= 2
    assert len(s.losers) >= 2
    # weights sum to 100
    w = basket_weights(s)
    assert sum(x[1] for x in w) == 100
    # winners/losers block names the seed tickers
    block = winners_losers_block(s)
    assert "HAL" in block and "GOLDBEES" in block
    assert "INDIGO" in block  # an avoid name


@pytest.mark.parametrize("text,expected", [
    ("I just got my first salary and I want my money to grow. Where do "
     "I even start?", True),
    ("bas batao, what should I buy this week? something solid", True),
    ("I want to make money. What should I do?", True),
    ("I have 50k, where do I start", True),
    ("analyse HDFC bank", False),
    ("buy 10 INFY when RSI<30", False),
    ("build me an iron condor on nifty", False),
    ("backtest a SMA crossover", False),
])
def test_vague_onboarding(text, expected):
    assert is_vague_onboarding(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("I have 2 lakh sitting idle in my savings account. I'm scared of "
     "losing money but FD returns feel pathetic. Do something.", True),
    ("I have 50k where do I start", False),
    ("buy 10 INFY", False),
])
def test_scared_idle_cash(text, expected):
    assert is_scared_idle_cash(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("make me 1% a day", True),
    ("double my money in a month", True),
    ("guaranteed 5% a week", True),
    ("give me 200% returns", True),
    ("get rich quick", True),
    ("start a 5000 monthly sip", False),
    ("what should I buy", False),
    ("analyse reliance", False),
])
def test_unrealistic_return(text, expected):
    assert is_unrealistic_return(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("I have 2 lakh", 200_000),
    ("around 75k", 75_000),
    ("₹1,00,000", 100_000),
    ("50k", 50_000),
    ("1 lakh", 100_000),
    ("no amount here", None),
])
def test_extract_capital(text, expected):
    assert extract_capital_inr(text) == expected
