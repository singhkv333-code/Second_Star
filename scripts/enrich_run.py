"""Non-interactive version of enrich_companies — runs the full pipeline."""
import re, sys, time, requests, psycopg2, pandas as pd
from io import StringIO
from rapidfuzz import fuzz, process

DB_PARAMS = dict(host='localhost', port=5432, user='postgres', password='@Tajmahal2', database='financials')
LOGO_TOKEN = 'pk_X3WtLGU0RTuTq-o9GTLEsg'
FUZZY_THRESHOLD = 80
LOGO_MIN_BYTES = 1000
LOGO_SLEEP = 0.05      # between domain attempts (reduced for throughput)
MATCH_SLEEP = 0.1      # after a successful match/logo found
NSE_CSV_URL = 'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv'
_STOP = frozenset({'limited','ltd','private','pvt','incorporated','inc','corporation','corp',
                   'industries','enterprise','enterprises','and','the','of','india','indian'})


def clean_name(name):
    name = name.lower()
    for s in [r'\blimited\b',r'\bltd\.?\b',r'\bprivate\b',r'\bpvt\.?\b',
              r'\bincorporated\b',r'\binc\.?\b',r'\bcorporation\b',r'\bcorp\.?\b',
              r'\benterprises\b',r'\benterprise\b',r'\bindustries\b']:
        name = re.sub(s, '', name)
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9\s]', ' ', name)).strip()


def _first_key(name):
    for w in re.split(r'[\s&.,()/-]+', name.lower()):
        if len(w) >= 4 and w not in _STOP:
            return w
    return None


def _plausible(db_name, nse_name):
    k = _first_key(db_name)
    return k is not None and k in nse_name.lower()


def match_ticker(sc_id, company_name, nse_df):
    direct = nse_df[nse_df['SYMBOL'] == sc_id.upper()]
    if not direct.empty:
        row = direct.iloc[0]
        return row['NAME OF COMPANY'], row['SYMBOL'], 100.0
    q = clean_name(company_name)
    r1_raw = process.extractOne(q, nse_df['clean'].tolist(), scorer=fuzz.token_sort_ratio, score_cutoff=FUZZY_THRESHOLD)
    r1 = r1_raw if r1_raw and _plausible(company_name, nse_df.iloc[r1_raw[2]]['NAME OF COMPANY']) else None
    r2_raw = process.extractOne(company_name, nse_df['NAME OF COMPANY'].tolist(), scorer=fuzz.partial_ratio, score_cutoff=FUZZY_THRESHOLD)
    r2 = r2_raw if r2_raw and _plausible(company_name, nse_df.iloc[r2_raw[2]]['NAME OF COMPANY']) else None
    best = None
    if r1 and r2: best = r2 if r2[1] > r1[1] else r1
    elif r1: best = r1
    elif r2: best = r2
    if not best: return None, None, 0.0
    row = nse_df.iloc[best[2]]
    return row['NAME OF COMPANY'], row['SYMBOL'], float(best[1])


def domain_candidates(company_name, ticker):
    """Return up to 3 domain guesses (most likely first)."""
    domains = []; t = ticker.lower()
    domains.append(f'{t}.com')
    clean = re.sub(r'[^a-z0-9]', '', company_name.lower())
    if len(clean) >= 3 and clean != t: domains.append(f'{clean}.com')
    domains.append(f'{t}.in')
    seen = set()
    return [d for d in domains if not (d in seen or seen.add(d))][:3]


def logo_url_for(company_name, ticker):
    for domain in domain_candidates(company_name, ticker):
        url = f'https://img.logo.dev/{domain}?token={LOGO_TOKEN}&size=128&format=png'
        try:
            r = requests.get(url, timeout=6); ct = r.headers.get('content-type', '')
            if r.status_code == 200 and 'image' in ct and len(r.content) > LOGO_MIN_BYTES:
                return url
        except: pass
        time.sleep(LOGO_SLEEP)
    return None


if __name__ == '__main__':
    conn = psycopg2.connect(**DB_PARAMS)

    # Ensure columns exist
    with conn.cursor() as cur:
        for col, dtype in [('ticker', 'VARCHAR(30)'), ('logo_url', 'VARCHAR(512)')]:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='mc' AND table_name='companies' AND column_name=%s", (col,))
            if cur.fetchone() is None:
                cur.execute(f'ALTER TABLE mc.companies ADD COLUMN {col} {dtype}')
                print(f'Added column: {col}', flush=True)
    conn.commit()

    # Load NSE master
    print('Downloading NSE CSV...', flush=True)
    r = requests.get(NSE_CSV_URL, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
    df = pd.read_csv(StringIO(r.text)); df.columns = df.columns.str.strip()
    df = df[['NAME OF COMPANY', 'SYMBOL']].dropna()
    df['clean'] = df['NAME OF COMPANY'].apply(clean_name)
    print(f'{len(df)} NSE equities loaded.', flush=True)

    # Fetch all rows
    with conn.cursor() as cur:
        cur.execute("""SELECT sc_id, company_name FROM mc.companies
            WHERE company_name NOT LIKE '%NSETEST%' AND length(company_name) > 3
            ORDER BY sc_id""")
        all_rows = cur.fetchall()
    print(f'Processing {len(all_rows)} companies...', flush=True)

    tickers_filled = 0; logos_found = 0; batch_size = 500
    for i in range(0, len(all_rows), batch_size):
        batch = all_rows[i:i+batch_size]
        updates = []
        for sc_id, cn in batch:
            nse_name, sym, score = match_ticker(sc_id, cn, df)
            logo = None
            if sym:
                logo = logo_url_for(cn, sym)
                time.sleep(MATCH_SLEEP)
            updates.append((sym, logo, sc_id))
            if sym: tickers_filled += 1
            if logo: logos_found += 1
        with conn.cursor() as cur:
            for sym, logo, sc_id in updates:
                cur.execute('UPDATE mc.companies SET ticker=%s, logo_url=%s WHERE sc_id=%s', (sym, logo, sc_id))
        conn.commit()
        done = min(i + batch_size, len(all_rows))
        print(f'  {done}/{len(all_rows)}  tickers={tickers_filled}  logos={logos_found}', flush=True)

    print(f'\n=== DONE ===', flush=True)
    print(f'Total    : {len(all_rows):,}', flush=True)
    print(f'Tickers  : {tickers_filled:,}', flush=True)
    print(f'Logos    : {logos_found:,}', flush=True)
    print(f'Nulls    : {len(all_rows)-tickers_filled:,}', flush=True)
    conn.close()
