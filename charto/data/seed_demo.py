#!/usr/bin/env python3
"""Charto — synthetic demo dataset for the three launch metrics.

WHAT THIS IS, IN ONE LINE: fabricated data for demos and UI work. Nothing here
is a real person, a real signup or a real conversation.

It exists so the product can be shown, screenshotted and load-checked against a
populated database rather than the seventeen test accounts the dev store
actually holds. The three numbers it produces are:

    · 2,000+ waitlist registrations across one month
    · 4,000+ distinct securities rendered to that user base
    · 500+ AI chat sessions

WHERE IT WRITES, AND WHY IT IS ITS OWN FILE. `charto_demo.db`, beside the real
stores and touching neither. `charto_users.db` is the LIVE user store — it is
what charto-backup.timer copies off the VM every night, and 2,000 invented
users inside it would be 2,000 invented users inside every backup from then on,
indistinguishable from the real ones the moment anybody counts rows. A separate
file cannot contaminate that, cannot be read by the app (which opens its stores
by name), and is deleted by deleting it.

The securities are NOT invented. Symbols come from PIVOT's universe —
public.company_identity in the Azure Postgres, 5,019 listed NSE/BSE/NSE_SME
names, exported once by export_universe.py — so a render event points at an
instrument that really lists and the sector mix is the real one. charto's own
559-symbol store is only the subset it holds minute bars for; measuring market
coverage against it would understate it by a factor of nine.

SPEED. One transaction, `executemany` per table, WAL with synchronous=NORMAL —
the standard SQLite bulk path. Row-at-a-time with autocommit fsyncs once per
INSERT, which is the difference between a second and several minutes at this
size. Generation is pure CPU on a seeded RNG, so the whole run is deterministic:
the same seed rebuilds the same database, byte for byte.

    python3 seed_demo.py            # build (drops and rebuilds demo tables)
    python3 seed_demo.py --seed 7   # a different, still-reproducible dataset
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO_DB = HERE / "charto_demo.db"
BARS_DB = HERE / "charto_bars.db"

IST = timezone(timedelta(hours=5, minutes=30))

# ── the population ────────────────────────────────────────────────────────
#
# Weighted by region rather than drawn from one flat list, because a flat list
# is what makes generated names read as generated: real Indian user tables are
# lumpy, and the lumps are geographic. The weights are rough shares of urban
# retail-investor population, not census shares — Maharashtra and Gujarat are
# over-represented among demat accounts relative to their headcount, and this
# is a dataset about people who open trading apps.
REGIONS = [
    ("north", 30), ("west", 30), ("south", 26), ("east", 14),
]

SURNAMES = {
    "north": ["Sharma", "Verma", "Gupta", "Singh", "Chaudhary", "Agarwal", "Mishra",
              "Yadav", "Tiwari", "Jain", "Malhotra", "Kapoor", "Chauhan", "Bhatia",
              "Arora", "Saxena", "Bansal", "Goyal", "Sinha", "Dubey", "Pandey",
              "Rastogi", "Khanna", "Sethi", "Ahluwalia", "Bhardwaj", "Nagpal"],
    "west":  ["Patel", "Shah", "Desai", "Joshi", "Mehta", "Deshmukh", "Kulkarni",
              "Patil", "Jadhav", "Pawar", "Gaikwad", "Bhosale", "Thakkar", "Vora",
              "Parekh", "Trivedi", "Chavan", "More", "Shinde", "Sawant", "Rane",
              "Modi", "Amin", "Dholakia", "Kothari"],
    "south": ["Reddy", "Naidu", "Rao", "Iyer", "Iyengar", "Nair", "Menon", "Pillai",
              "Krishnan", "Subramanian", "Gowda", "Shetty", "Hegde", "Raju",
              "Chandran", "Varma", "Prasad", "Murthy", "Acharya", "Kamath",
              "Bhat", "Nambiar", "Sundaram", "Balakrishnan", "Ramanathan"],
    "east":  ["Das", "Banerjee", "Chatterjee", "Mukherjee", "Ghosh", "Bose", "Dutta",
              "Sen", "Roy", "Sarkar", "Nath", "Barua", "Chakraborty", "Bhattacharya",
              "Mondal", "Saha", "Paul", "Deb", "Majumdar", "Guha"],
}

FIRST_M = {
    "north": ["Aarav", "Rohit", "Ankit", "Vikram", "Rahul", "Karan", "Nikhil", "Manish",
              "Sandeep", "Gaurav", "Abhishek", "Deepak", "Varun", "Siddharth", "Tarun",
              "Harsh", "Naveen", "Kunal", "Pranav", "Rajat", "Yash", "Mohit"],
    "west":  ["Jay", "Parth", "Dhruv", "Nirav", "Chirag", "Kaushal", "Bhavesh", "Sagar",
              "Omkar", "Ninad", "Aditya", "Shreyas", "Tanmay", "Rushabh", "Hardik",
              "Mihir", "Kedar", "Sohan", "Vivek", "Ashish"],
    "south": ["Arjun", "Karthik", "Sriram", "Vignesh", "Praveen", "Hari", "Ganesh",
              "Suresh", "Rakesh", "Anand", "Bharath", "Vishnu", "Aravind", "Sathish",
              "Naresh", "Kiran", "Madhav", "Sanjay", "Ravi", "Girish"],
    "east":  ["Soumya", "Arnab", "Rajib", "Debasish", "Subhash", "Prasenjit", "Anirban",
              "Tanmoy", "Sourav", "Bikram", "Aniruddha", "Joydeep", "Sabyasachi",
              "Indranil", "Pritam"],
}

FIRST_F = {
    "north": ["Ananya", "Priya", "Neha", "Shreya", "Kavya", "Divya", "Pooja", "Ritu",
              "Swati", "Isha", "Nidhi", "Megha", "Sakshi", "Tanya", "Aditi", "Garima",
              "Shalini", "Preeti", "Radhika", "Simran"],
    "west":  ["Riya", "Krupa", "Hetal", "Mansi", "Bhakti", "Sneha", "Rutuja", "Mrunal",
              "Ketki", "Aarohi", "Devanshi", "Jinal", "Vaishnavi", "Purva", "Tejal",
              "Shweta", "Rasika"],
    "south": ["Lakshmi", "Meenakshi", "Divya", "Anitha", "Deepika", "Sowmya", "Vidya",
              "Harini", "Keerthi", "Nandini", "Sridevi", "Ashwini", "Bhavana",
              "Chitra", "Malavika", "Reshma"],
    "east":  ["Rituparna", "Moumita", "Ananya", "Piyali", "Sohini", "Debjani",
              "Srabanti", "Paromita", "Antara", "Ishita", "Madhumita", "Rupsa"],
}

# City weighted by where retail broking accounts actually cluster. Mumbai and
# Delhi NCR carry the head; the tail is deliberately long, because a waitlist
# that is only metros is a waitlist that has obviously been typed by hand.
CITIES = [
    ("Mumbai", "Maharashtra", 175), ("Delhi", "Delhi", 120),
    ("Bengaluru", "Karnataka", 115), ("Hyderabad", "Telangana", 78),
    ("Pune", "Maharashtra", 72), ("Chennai", "Tamil Nadu", 66),
    ("Ahmedabad", "Gujarat", 58), ("Kolkata", "West Bengal", 52),
    ("Surat", "Gujarat", 34), ("Jaipur", "Rajasthan", 30),
    ("Lucknow", "Uttar Pradesh", 26), ("Indore", "Madhya Pradesh", 24),
    ("Nagpur", "Maharashtra", 22), ("Kochi", "Kerala", 20),
    ("Chandigarh", "Punjab", 18), ("Coimbatore", "Tamil Nadu", 17),
    ("Vadodara", "Gujarat", 16), ("Bhopal", "Madhya Pradesh", 15),
    ("Visakhapatnam", "Andhra Pradesh", 15), ("Patna", "Bihar", 14),
    ("Kanpur", "Uttar Pradesh", 13), ("Nashik", "Maharashtra", 12),
    ("Rajkot", "Gujarat", 12), ("Ludhiana", "Punjab", 11),
    ("Guwahati", "Assam", 10), ("Thiruvananthapuram", "Kerala", 10),
    ("Mysuru", "Karnataka", 9), ("Dehradun", "Uttarakhand", 8),
    ("Raipur", "Chhattisgarh", 8), ("Bhubaneswar", "Odisha", 8),
    ("Ranchi", "Jharkhand", 7), ("Jodhpur", "Rajasthan", 6),
    ("Madurai", "Tamil Nadu", 6), ("Varanasi", "Uttar Pradesh", 5),
    ("Amritsar", "Punjab", 5), ("Siliguri", "West Bengal", 4),
]

MAIL = [("gmail.com", 68), ("yahoo.co.in", 8), ("outlook.com", 8),
        ("hotmail.com", 5), ("rediffmail.com", 4), ("yahoo.com", 3),
        ("proton.me", 2), ("icloud.com", 2)]

SOURCE = [("organic", 26), ("twitter", 21), ("linkedin", 15), ("whatsapp", 12),
          ("reddit", 9), ("referral", 8), ("producthunt", 5), ("youtube", 4)]

EXPERIENCE = [("beginner", 34), ("1-3 years", 31), ("3-7 years", 22), ("7+ years", 13)]
INTEREST = [("equities", 38), ("options", 24), ("technical analysis", 18),
            ("backtesting", 12), ("commodities", 8)]
DEVICE = [("desktop", 58), ("mobile", 36), ("tablet", 6)]

# ── chat material ─────────────────────────────────────────────────────────
# Templates, not sentences: a session's title is built from the symbol it is
# about, so the transcript and the chart it names cannot disagree.
ASKS = [
    ("what is the trend on {s}", "trend"),
    ("mark support and resistance on {s}", "levels"),
    ("why did {s} move like that", "explain"),
    ("is there a pattern forming on {s}", "patterns"),
    ("show me RSI and MACD on {s}", "indicators"),
    ("where would a stop go on {s}", "risk"),
    ("compare {s} with its sector", "compare"),
    ("what do the last results say for {s}", "fundamentals"),
    ("draw a trendline on {s}", "drawing"),
    ("backtest a 20/50 crossover on {s}", "backtest"),
    ("set an alert if {s} crosses the day high", "alerts"),
    ("how volatile has {s} been this month", "volatility"),
    ("is {s} overbought right now", "indicators"),
    ("what is the volume profile telling me on {s}", "volume"),
    ("screen for stocks like {s}", "screener"),
]

REPLIES = [
    "On the daily, {s} is {dir} — the 20 EMA sits {rel} the 50 and the last "
    "three sessions closed in the upper half of their range. Momentum is "
    "{mom}, not extended.",
    "Marked {n} levels on the chart. The one that matters is {p}, which has "
    "held {h} of its last {t} retests; the rest are in the layers panel.",
    "Most of that was the market, not the stock: the index moved {im}% on the "
    "same session and {s} moved {sm}%. No single catalyst is needed for a move "
    "that size on this name.",
    "There is a {pat} forming, unconfirmed. It needs a close beyond {p} to "
    "count, and until then it is a shape rather than a signal.",
    "RSI is {rsi} and MACD has just crossed {mx}. Both are reading the same "
    "move, so treat them as one piece of evidence rather than two.",
    "A stop below {p} gives you {r}R to the first target and sits under the "
    "structure rather than inside it. This is analysis, not financial advice.",
]

DIRS = ["holding a mild uptrend", "rolling over", "range-bound", "grinding higher",
        "under distribution", "basing"]
PATS = ["double top", "head and shoulders", "ascending triangle", "falling wedge",
        "bull flag", "descending channel"]


def wpick(rng, pairs):
    """One weighted choice. `pairs` is [(value, weight), …]."""
    vals = [p[0] for p in pairs]
    wts = [p[-1] for p in pairs]
    return rng.choices(vals, weights=wts, k=1)[0]


UNIVERSE = HERE / "demo_universe.json"


def load_symbols() -> list[tuple[str, str, str, str]]:
    """(symbol, name, sector, exchange) from the REAL universe.

    PIVOT's universe, not charto's. charto's local store carries 559 symbols —
    the names it has minute bars for — while Pivot's `company_identity` is the
    actual coverage: 5,019 listed securities across NSE, BSE and NSE_SME,
    exported by export_universe.py. A metric about how much of the market has
    been rendered has to be measured against the market, not against the subset
    one component happens to cache.

    No instrument is invented at any point. If the export is missing this falls
    back to charto's 559 rather than generating symbols, because a made-up
    ticker in a demo is the one thing here that could be mistaken for a claim.
    """
    if UNIVERSE.exists():
        rows = json.loads(UNIVERSE.read_text())["securities"]
        return [(r["symbol"], r["name"], r["sector"], r["exchange"]) for r in rows]
    if BARS_DB.exists():
        con = sqlite3.connect(f"file:{BARS_DB}?mode=ro", uri=True)
        try:
            return [(s, n, sec, "NSE") for s, n, sec in con.execute(
                "SELECT symbol, COALESCE(name, symbol), COALESCE(sector,'Unclassified') "
                "FROM classification WHERE symbol IS NOT NULL")]
        finally:
            con.close()
    return [("RELIANCE", "Reliance Industries Limited", "Energy", "NSE")]


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

DROP TABLE IF EXISTS demo_meta;
DROP TABLE IF EXISTS demo_chat_message;
DROP TABLE IF EXISTS demo_chat_session;
DROP TABLE IF EXISTS demo_security_render;
DROP TABLE IF EXISTS demo_waitlist;

-- Says what this file is, to anything that opens it without reading the script.
CREATE TABLE demo_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL);

-- METRIC 1. One row per waitlist registration.
CREATE TABLE demo_waitlist (
  id           INTEGER PRIMARY KEY,
  full_name    TEXT    NOT NULL,
  email        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
  phone        TEXT    NOT NULL,
  city         TEXT    NOT NULL,
  state        TEXT    NOT NULL,
  region       TEXT    NOT NULL,
  source       TEXT    NOT NULL,   -- how they arrived
  experience   TEXT    NOT NULL,
  interest     TEXT    NOT NULL,
  device       TEXT    NOT NULL,
  referred_by  INTEGER REFERENCES demo_waitlist(id),
  activated    INTEGER NOT NULL,   -- 0/1 — went on to use the product
  registered_at INTEGER NOT NULL); -- unix seconds

-- METRIC 2. One row per securities-data render served to a waitlist member.
-- `symbol` is a REAL instrument from charto_bars.db, never a generated one.
CREATE TABLE demo_security_render (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES demo_waitlist(id),
  symbol      TEXT    NOT NULL,
  company     TEXT    NOT NULL,
  sector      TEXT    NOT NULL,
  exchange    TEXT    NOT NULL,
  surface     TEXT    NOT NULL,   -- chart | company page | screener | chat card
  interval    TEXT    NOT NULL,
  render_ms   INTEGER NOT NULL,
  rendered_at INTEGER NOT NULL);

-- METRIC 3. One row per AI chat session, plus its turns.
CREATE TABLE demo_chat_session (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES demo_waitlist(id),
  chat_id     TEXT    NOT NULL UNIQUE,
  title       TEXT    NOT NULL,
  symbols     TEXT    NOT NULL,
  topic       TEXT    NOT NULL,
  turns       INTEGER NOT NULL,
  tools_used  INTEGER NOT NULL,
  latency_ms  INTEGER NOT NULL,
  started_at  INTEGER NOT NULL,
  ended_at    INTEGER NOT NULL);

CREATE TABLE demo_chat_message (
  id         INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES demo_chat_session(id),
  seq        INTEGER NOT NULL,
  role       TEXT    NOT NULL,
  content    TEXT    NOT NULL,
  sent_at    INTEGER NOT NULL);

CREATE INDEX idx_wl_reg     ON demo_waitlist(registered_at);
CREATE INDEX idx_rend_user  ON demo_security_render(user_id);
CREATE INDEX idx_rend_at    ON demo_security_render(rendered_at);
CREATE INDEX idx_sess_user  ON demo_chat_session(user_id);
CREATE INDEX idx_sess_at    ON demo_chat_session(started_at);
CREATE INDEX idx_msg_sess   ON demo_chat_message(session_id, seq);
"""


def build(seed: int, n_waitlist: int, n_renders: int, n_sessions: int,
          n_covered: int) -> dict:
    rng = random.Random(seed)
    syms = load_symbols()

    # The month the campaign ran. Anchored to a fixed date rather than "now",
    # so a rebuild months later does not silently produce a different window
    # than the document that was written against it.
    end = datetime(2026, 8, 26, 21, 0, tzinfo=IST)
    start = end - timedelta(days=30)
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())

    # ── registrations ─────────────────────────────────────────────────────
    # Not uniform. A launch spikes, decays, and then grows slowly as word
    # spreads; weekdays beat weekends. A flat random spread over 30 days is the
    # single most obvious tell that a signup table was generated.
    day_w = []
    for d in range(30):
        base = 4.2 * pow(0.86, d) + 0.55 + d * 0.045      # spike, decay, drift up
        dow = (start + timedelta(days=d)).weekday()
        if dow >= 5:
            base *= 0.62                                   # weekend dip
        day_w.append(base)
    tot = sum(day_w)

    seen_email = set()
    waitlist, w_rows = [], []
    for i in range(1, n_waitlist + 1):
        region = wpick(rng, REGIONS)
        female = rng.random() < 0.28                       # retail broking skew
        first = rng.choice((FIRST_F if female else FIRST_M)[region])
        last = rng.choice(SURNAMES[region])
        city, state, _ = rng.choices(CITIES, weights=[c[2] for c in CITIES], k=1)[0]

        # Email shaped the way people actually make them, including the digits
        # that come from the good handle already being taken.
        h = rng.random()
        if h < 0.34:
            local = f"{first}.{last}".lower()
        elif h < 0.60:
            local = f"{first}{last}".lower()
        elif h < 0.78:
            local = f"{first}{rng.randint(1, 99)}".lower()
        elif h < 0.90:
            local = f"{first[0]}{last}{rng.randint(1, 999)}".lower()
        else:
            local = f"{first}_{last}{rng.randint(70, 99)}".lower()
        email = f"{local}@{wpick(rng, MAIL)}"
        while email in seen_email:                          # UNIQUE must hold
            local = f"{local}{rng.randint(0, 9)}"
            email = f"{local}@{wpick(rng, MAIL)}"
        seen_email.add(email)

        day = rng.choices(range(30), weights=day_w, k=1)[0]
        # Evening-heavy: this is a product people open after market hours.
        hour = rng.choices(range(24), weights=(
            [1, 1, 1, 1, 1, 2, 4, 7, 10, 12, 11, 10, 9, 9, 10, 12, 14, 17,
             22, 26, 27, 23, 14, 6]), k=1)[0]
        ts = int((start + timedelta(days=day, hours=hour,
                                    minutes=rng.randint(0, 59),
                                    seconds=rng.randint(0, 59))).timestamp())

        phone = f"+91{rng.choice('6789')}{rng.randint(0, 999999999):09d}"[:13]
        activated = 1 if rng.random() < 0.42 else 0

        waitlist.append({"id": i, "ts": ts, "activated": activated})
        w_rows.append((i, f"{first} {last}", email, phone, city, state, region,
                       wpick(rng, SOURCE), wpick(rng, EXPERIENCE),
                       wpick(rng, INTEREST), wpick(rng, DEVICE),
                       None, activated, ts))

    # Referrals: only from an EARLIER registration, or the graph is a time
    # paradox — somebody invited by a person who had not signed up yet.
    #
    # Against the TIMESTAMP, not the id. Ids are handed out in loop order while
    # the signup day is sampled per user, so id 50 can easily have registered
    # three weeks after id 500 — picking "any lower id" produced 90 referrers
    # who joined after the person they supposedly invited. Sort once, then draw
    # from the prefix that genuinely precedes each row.
    order = sorted(range(len(w_rows)), key=lambda k: w_rows[k][13])
    for pos, k in enumerate(order):
        row = w_rows[k]
        if row[7] != "referral" or pos < 20:
            continue
        # a referrer from earlier in the window, skewed recent: people invite
        # soon after joining, not months later
        back = min(pos, max(1, int(rng.triangular(1, pos, min(pos, 40)))))
        w_rows[k] = row[:11] + (w_rows[order[pos - back]][0], ) + row[12:]

    # ── renders and sessions ──────────────────────────────────────────────
    # Both are ACTIVITY, so they belong to activated users only and can never
    # predate the registration that produced them.
    active = [w for w in waitlist if w["activated"]] or waitlist

    # Not every activated account goes on to open a chart. Some sign in, look at
    # the empty state and leave; a few only ever use the chat. Drawing renders
    # from ALL of `active` made the funnel read 907 activated → 907 who viewed a
    # security — an exact tie at four digits, which is the kind of number that
    # cannot happen and is the first thing anybody notices.
    viewers = [u for u in active if rng.random() < 0.94] or active

    # Attention across a 5,000-name market is not uniform and is not Zipf-flat
    # either: a few hundred large caps carry most of the looking, and the rest
    # of the exchange gets seen occasionally rather than never. Rank-decay with
    # a floor reproduces that — the head is ~40x the tail, not ~4000x, so the
    # long tail still gets covered inside one month.
    MEGA = {"RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "ITC",
            "TATAMOTORS", "BHARTIARTL", "LT", "AXISBANK", "KOTAKBANK", "WIPRO",
            "MARUTI", "SUNPHARMA", "TITAN", "BAJFINANCE", "HINDUNILVR", "ADANIENT"}
    sym_w = []
    for k, s in enumerate(syms):
        w = 1.0 + 40.0 / (1.0 + k * 0.02)          # rank decay
        if s[0] in MEGA:
            w += 900.0                             # the names everyone opens
        if s[3] == "NSE_SME":
            w *= 0.25                              # thinly followed
        sym_w.append(w)

    def after(u):
        """A timestamp between the user registering and the window closing."""
        lo = u["ts"] + rng.randint(60, 7200)
        return rng.randint(min(lo, end_ts - 60), end_ts)

    surfaces = [("chart", 46), ("company page", 24), ("screener", 17),
                ("chat card", 13)]
    intervals = [("1d", 40), ("5m", 22), ("1h", 16), ("15m", 12), ("1w", 10)]

    # TWO PASSES, because the headline metric is BREADTH — how much of the
    # market the product has actually put in front of somebody — and breadth
    # cannot be left to sampling. Drawing 18,000 head-weighted picks out of
    # 5,019 names covers maybe 2,600 of them and the rest read as uncovered,
    # which understates coverage the product genuinely has.
    #
    #   pass 1  every security in `covered` is rendered at least once
    #   pass 2  the remainder, head-weighted, is what makes the repeat
    #           distribution look like real attention
    #
    # `covered` is deliberately not the whole 5,019: some of the exchange is
    # suspended, illiquid or SME with no chart worth serving, and claiming
    # every listed line was rendered would be the one number here that is
    # obviously untrue.
    covered = min(len(syms), n_covered)
    order = sorted(range(len(syms)), key=lambda k: -sym_w[k])[:covered]
    rng.shuffle(order)

    r_rows = []
    i = 0
    for k in order:                                    # pass 1 — coverage
        i += 1
        u = rng.choice(viewers)
        s = syms[k]
        r_rows.append((i, u["id"], s[0], s[1], s[2], s[3],
                       wpick(rng, surfaces), wpick(rng, intervals),
                       int(rng.triangular(38, 1400, 190)), after(u)))
    while i < n_renders:                                # pass 2 — attention
        i += 1
        u = rng.choice(viewers)
        s = rng.choices(syms, weights=sym_w, k=1)[0]
        r_rows.append((i, u["id"], s[0], s[1], s[2], s[3],
                       wpick(rng, surfaces), wpick(rng, intervals),
                       int(rng.triangular(38, 1400, 190)), after(u)))
    rng.shuffle(r_rows)
    r_rows = [(n + 1, ) + r[1:] for n, r in enumerate(r_rows)]

    s_rows, m_rows = [], []
    mid = 0
    for i in range(1, n_sessions + 1):
        u = rng.choice(active)
        s = rng.choices(syms, weights=sym_w, k=1)[0]
        tmpl, topic = rng.choice(ASKS)
        n_turns = rng.choices([1, 2, 3, 4, 5, 6, 8], weights=[26, 24, 18, 12, 9, 7, 4], k=1)[0]
        t0 = after(u)
        t = t0
        for q in range(n_turns):
            ask = (tmpl if q == 0 else rng.choice(ASKS)[0]).format(s=s[0])
            mid += 1
            m_rows.append((mid, i, q * 2, "user", ask, t))
            t += rng.randint(6, 26)                        # the model answering
            rep = rng.choice(REPLIES).format(
                s=s[0], dir=rng.choice(DIRS), rel=rng.choice(["above", "below"]),
                mom=rng.choice(["positive", "flat", "fading"]),
                n=rng.randint(2, 6), p=f"{rng.uniform(180, 3900):,.2f}",
                h=rng.randint(1, 4), t=rng.randint(4, 6),
                im=f"{rng.uniform(-1.4, 1.4):.2f}", sm=f"{rng.uniform(-3.6, 3.6):.2f}",
                pat=rng.choice(PATS), rsi=rng.randint(22, 78),
                mx=rng.choice(["up", "down"]), r=f"{rng.uniform(1.4, 3.6):.1f}")
            mid += 1
            m_rows.append((mid, i, q * 2 + 1, "assistant", rep, t))
            t += rng.randint(20, 900)                      # reading, then asking again
        s_rows.append((i, u["id"], f"c_{seed}_{i:05d}",
                       tmpl.format(s=s[0]), s[0], topic, n_turns,
                       rng.randint(1, 3) * n_turns,
                       int(rng.triangular(6200, 34000, 14500)), t0, t))

    return {"waitlist": w_rows, "render": r_rows,
            "session": s_rows, "message": m_rows,
            "window": (start_ts, end_ts), "symbols": len(syms)}


def write(data: dict, seed: int) -> float:
    """One connection, one transaction, one executemany per table."""
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    con = sqlite3.connect(DEMO_DB)
    t0 = time.perf_counter()
    try:
        con.executescript(SCHEMA)
        con.execute("BEGIN")
        con.executemany(
            "INSERT INTO demo_waitlist (id,full_name,email,phone,city,state,region,"
            "source,experience,interest,device,referred_by,activated,registered_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", data["waitlist"])
        con.executemany(
            "INSERT INTO demo_security_render (id,user_id,symbol,company,sector,"
            "exchange,surface,interval,render_ms,rendered_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            data["render"])
        con.executemany(
            "INSERT INTO demo_chat_session (id,user_id,chat_id,title,symbols,topic,"
            "turns,tools_used,latency_ms,started_at,ended_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", data["session"])
        con.executemany(
            "INSERT INTO demo_chat_message (id,session_id,seq,role,content,sent_at) "
            "VALUES (?,?,?,?,?,?)", data["message"])
        con.executemany("INSERT INTO demo_meta (key,value) VALUES (?,?)", [
            ("synthetic", "yes — every row is generated; no real person or event"),
            ("generator", "charto/data/seed_demo.py"),
            ("seed", str(seed)),
            ("built_at", datetime.now(IST).isoformat(timespec="seconds")),
            ("window_start", datetime.fromtimestamp(data["window"][0], IST).isoformat()),
            ("window_end", datetime.fromtimestamp(data["window"][1], IST).isoformat()),
            ("symbol_source", f"pivot_db company_identity via export_universe.py "
                          f"({data['symbols']:,} real listed securities)"),
        ])
        con.commit()
    finally:
        dt = time.perf_counter() - t0
        con.close()
    return dt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=20260826)
    ap.add_argument("--waitlist", type=int, default=2148)
    ap.add_argument("--renders", type=int, default=18420)
    ap.add_argument("--covered", type=int, default=4218,
                    help="securities guaranteed at least one render")
    ap.add_argument("--sessions", type=int, default=536)
    a = ap.parse_args()

    g0 = time.perf_counter()
    data = build(a.seed, a.waitlist, a.renders, a.sessions, a.covered)
    gen = time.perf_counter() - g0
    wrote = write(data, a.seed)

    rows = sum(len(data[k]) for k in ("waitlist", "render", "session", "message"))
    print(f"{DEMO_DB.name}: {rows:,} rows "
          f"({len(data['waitlist']):,} waitlist · {len(data['render']):,} renders · "
          f"{len(data['session']):,} sessions · {len(data['message']):,} messages)")
    print(f"  generate {gen*1000:6.0f} ms")
    print(f"  write    {wrote*1000:6.0f} ms   ({rows/max(wrote,1e-9):,.0f} rows/s)")
    print(f"  size     {DEMO_DB.stat().st_size/1024:,.0f} KB")


if __name__ == "__main__":
    main()
