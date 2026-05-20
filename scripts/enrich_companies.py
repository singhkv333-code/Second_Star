"""
Enrich mc.companies with NSE tickers (ticker column) and logos (logo_url column).

Steps:
  1. Add ticker / logo_url columns if missing
  2. Download NSE equity master CSV
  3. Match: sc_id direct → token_sort_ratio → partial_ratio (both gated by plausibility)
  4. Dry-run 20 real rows, print table, ask for confirmation
  5. Full update + logo fetch via logo.dev
"""

import re
import sys
import time

import psycopg2
import requests
import pandas as pd
from rapidfuzz import fuzz, process

# ── credentials ─────────────────────────────────────────────────────────────
DB_PARAMS = dict(
    host="localhost",
    port=5432,
    user="postgres",
    password="@Tajmahal2",
    database="financials",
)
LOGO_TOKEN      = "pk_X3WtLGU0RTuTq-o9GTLEsg"
FUZZY_THRESHOLD = 80
LOGO_MIN_BYTES  = 1000
NSE_CSV_URL     = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

_STOP_WORDS = frozenset({
    "limited", "ltd", "private", "pvt", "incorporated", "inc",
    "corporation", "corp", "industries", "enterprise", "enterprises",
    "and", "the", "of", "india", "indian",
})

# ── helpers ──────────────────────────────────────────────────────────────────

def clean_name(name: str) -> str:
    name = name.lower()
    for suffix in [
        r"\blimited\b", r"\bltd\.?\b", r"\bprivate\b", r"\bpvt\.?\b",
        r"\bincorporated\b", r"\binc\.?\b", r"\bcorporation\b", r"\bcorp\.?\b",
        r"\benterprises\b", r"\benterprise\b", r"\bindustries\b",
    ]:
        name = re.sub(suffix, "", name)
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _first_distinctive_word(name: str) -> str | None:
    for word in re.split(r"[\s&.,()/-]+", name.lower()):
        if len(word) >= 4 and word not in _STOP_WORDS:
            return word
    return None


def _plausible(db_name: str, nse_name: str) -> bool:
    """First distinctive word of db_name must appear in nse_name."""
    key = _first_distinctive_word(db_name)
    return key is not None and key in nse_name.lower()


def match_ticker(
    sc_id: str, company_name: str, nse_df: pd.DataFrame
) -> tuple[str | None, str | None, float]:
    # 1. Direct sc_id → NSE SYMBOL
    direct = nse_df[nse_df["SYMBOL"] == sc_id.upper()]
    if not direct.empty:
        row = direct.iloc[0]
        return row["NAME OF COMPANY"], row["SYMBOL"], 100.0

    # 2. token_sort_ratio on cleaned names + plausibility guard
    q = clean_name(company_name)
    r1_raw = process.extractOne(
        q, nse_df["clean"].tolist(),
        scorer=fuzz.token_sort_ratio,
        score_cutoff=FUZZY_THRESHOLD,
    )
    r1: tuple | None = None
    if r1_raw is not None:
        if _plausible(company_name, nse_df.iloc[r1_raw[2]]["NAME OF COMPANY"]):
            r1 = r1_raw

    # 3. partial_ratio + plausibility guard (handles truncated names)
    r2_raw = process.extractOne(
        company_name, nse_df["NAME OF COMPANY"].tolist(),
        scorer=fuzz.partial_ratio,
        score_cutoff=FUZZY_THRESHOLD,
    )
    r2: tuple | None = None
    if r2_raw is not None:
        if _plausible(company_name, nse_df.iloc[r2_raw[2]]["NAME OF COMPANY"]):
            r2 = r2_raw

    best: tuple | None = None
    if r1 and r2:
        best = r2 if r2[1] > r1[1] else r1
    elif r1:
        best = r1
    elif r2:
        best = r2

    if best is None:
        return None, None, 0.0

    row = nse_df.iloc[best[2]]
    return row["NAME OF COMPANY"], row["SYMBOL"], float(best[1])


def domain_candidates(company_name: str, ticker: str) -> list[str]:
    domains: list[str] = []
    t = ticker.lower()
    domains += [f"{t}.com", f"{t}.in"]
    clean = re.sub(r"[^a-z0-9]", "", company_name.lower())
    if len(clean) >= 3:
        domains += [f"{clean}.com", f"{clean}.in"]
    words = [w for w in company_name.lower().split() if len(w) > 2]
    if len(words) >= 2:
        domains.append(f"{words[0]}{words[1]}.com")
    seen: set[str] = set()
    return [d for d in domains if not (d in seen or seen.add(d))]  # type: ignore[func-returns-value]


def logo_url_for(company_name: str, ticker: str) -> str | None:
    for domain in domain_candidates(company_name, ticker):
        url = f"https://img.logo.dev/{domain}?token={LOGO_TOKEN}&size=128&format=png"
        try:
            r = requests.get(url, timeout=8)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and "image" in ct and len(r.content) > LOGO_MIN_BYTES:
                return url
        except requests.RequestException:
            pass
        time.sleep(0.15)
    return None


# ── DB helpers ────────────────────────────────────────────────────────────────

def add_columns(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        for col, dtype in [("ticker", "VARCHAR(30)"), ("logo_url", "VARCHAR(512)")]:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='mc' AND table_name='companies' AND column_name=%s",
                (col,),
            )
            if cur.fetchone() is None:
                cur.execute(f"ALTER TABLE mc.companies ADD COLUMN {col} {dtype}")
                print(f"  Added column: {col}")
            else:
                print(f"  Column already exists: {col}")
    conn.commit()


def load_nse_master() -> pd.DataFrame:
    print("Downloading NSE equity master CSV …")
    r = requests.get(NSE_CSV_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    df.columns = df.columns.str.strip()
    df = df[["NAME OF COMPANY", "SYMBOL"]].dropna()
    df["clean"] = df["NAME OF COMPANY"].apply(clean_name)
    print(f"  {len(df)} NSE equities loaded.")
    return df


def process_batch(
    companies: list[tuple[str, str]],
    nse_df: pd.DataFrame,
    fetch_logos: bool = True,
) -> list[tuple]:
    results = []
    for sc_id, company_name in companies:
        nse_name, symbol, score = match_ticker(sc_id, company_name, nse_df)
        logo = None
        if symbol and fetch_logos:
            logo = logo_url_for(company_name, symbol)
            time.sleep(0.3)
        results.append((sc_id, company_name, nse_name, score, symbol, logo))
    return results


def print_table(results: list[tuple]) -> None:
    print(f"\n{'DB Company':<22} {'Matched NSE Name':<45} {'Score':>5}  {'Ticker':<14} Logo?")
    print("-" * 100)
    for sc_id, cn, nse, score, sym, logo in results:
        print(
            f"{cn:<22} {(nse or '— no match —'):<45} {score:>5.1f}  "
            f"{(sym or 'NULL'):<14} {'YES' if logo else 'no'}"
        )


def apply_updates(results: list[tuple], conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        for sc_id, _cn, _nse, _score, symbol, logo in results:
            cur.execute(
                "UPDATE mc.companies SET ticker=%s, logo_url=%s WHERE sc_id=%s",
                (symbol, logo, sc_id),
            )
    conn.commit()


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    conn = psycopg2.connect(**DB_PARAMS)

    print("=== Step 1: Adding columns ===")
    add_columns(conn)

    print("\n=== Step 2: Loading NSE master ===")
    nse_df = load_nse_master()

    # ── Dry run ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DRY RUN — 20 real companies (non-test rows)")
    print("=" * 60)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT sc_id, company_name FROM mc.companies
            WHERE company_name NOT LIKE '%NSETEST%'
              AND length(company_name) > 3
            ORDER BY discovered_at DESC
            LIMIT 20
        """)
        sample = cur.fetchall()

    dry_results = process_batch(sample, nse_df, fetch_logos=True)
    print_table(dry_results)

    matched = sum(1 for r in dry_results if r[4])
    logos   = sum(1 for r in dry_results if r[5])
    print(f"\nMatched: {matched}/{len(dry_results)}  |  Logos found: {logos}/{max(matched, 1)}")

    print("\n" + "=" * 60)
    answer = input("Apply dry-run updates and continue to FULL run? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted. No changes written.")
        conn.close()
        sys.exit(0)

    apply_updates(dry_results, conn)
    print(f"  Dry-run rows saved.")

    # ── Full run ─────────────────────────────────────────────────────────────
    print("\n=== FULL RUN — fetching all remaining rows ===")
    already = {r[0] for r in dry_results}

    with conn.cursor() as cur:
        cur.execute("""
            SELECT sc_id, company_name FROM mc.companies
            WHERE company_name NOT LIKE '%NSETEST%'
              AND length(company_name) > 3
            ORDER BY sc_id
        """)
        all_rows = [(sid, cn) for sid, cn in cur.fetchall() if sid not in already]

    print(f"Processing {len(all_rows)} companies …")

    full_results: list[tuple] = []
    batch_size = 200
    for i in range(0, len(all_rows), batch_size):
        batch = all_rows[i : i + batch_size]
        batch_results = process_batch(batch, nse_df, fetch_logos=True)
        apply_updates(batch_results, conn)
        full_results.extend(batch_results)
        done = i + len(batch)
        matched_so_far = sum(1 for r in full_results if r[4])
        logos_so_far   = sum(1 for r in full_results if r[5])
        print(f"  {done}/{len(all_rows)}  —  tickers: {matched_so_far}  logos: {logos_so_far}")

    # ── Summary ───────────────────────────────────────────────────────────────
    all_results = dry_results + full_results
    total          = len(all_results)
    tickers_filled = sum(1 for r in all_results if r[4])
    logos_found    = sum(1 for r in all_results if r[5])
    nulls          = total - tickers_filled

    print(f"\n{'='*60}")
    print(f"Final summary")
    print(f"{'='*60}")
    print(f"  Total rows processed : {total:,}")
    print(f"  Tickers filled       : {tickers_filled:,}")
    print(f"  Logos found          : {logos_found:,}")
    print(f"  Ticker nulls remain  : {nulls:,}")

    failed = [(r[1], r[3]) for r in all_results if not r[4]]
    if failed:
        print(f"\nTop 20 unmatched (likely delisted / SME / ETF):")
        for name, score in sorted(failed, key=lambda x: x[1])[:20]:
            print(f"  {name}")

    conn.close()
