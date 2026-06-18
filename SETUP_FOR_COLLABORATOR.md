# Pivot — collaborator setup (shared cloud DB)

This gets you running Pivot **against the same Azure (Central India) databases** the
owner uses, so you share live data. You run the app locally; only the databases
and the LLM/Kite credentials are shared.

> The code in this repo carries **no secrets** (`.env*` is gitignored). You'll
> get the credentials from the owner out-of-band (see step 3). Don't commit them.

---

## 0. Prerequisites
- **Python 3.11** (the backend is pinned to 3.11)
- **Node 18+** and **pnpm** (`npm i -g pnpm`)
- **Redis** running locally (`redis-server`, or `brew install redis && brew services start redis`) — Redis is **not** shared; each dev runs their own.
- **git**, and (optional) the `psql` client (`brew install libpq`) if you want to poke the DB directly.

## 1. Clone + branch
```bash
git clone https://github.com/singhkv333-code/Second_Star.git
cd Second_Star
git checkout Eventtriggers      # the active branch
```

## 2. Firewall — nothing to do (dev DB is open to all IPs)
The `pivot-db-india` server has an `allow-all-dev` rule (`0.0.0.0–255.255.255.255`),
so you can connect from any IP without asking the owner to allowlist you. The DB is
protected by the `pivotadmin` password + `sslmode=require` only — **do not** put real
secrets or production data on it, and don't share the connection string publicly.

> Owner note: to lock it back down, delete the open rule and add per-IP rules again:
> ```bash
> az postgres flexible-server firewall-rule delete -g pivot -s pivot-db-india -n allow-all-dev
> az postgres flexible-server firewall-rule create -g pivot -s pivot-db-india \
>   -n <name>-laptop --start-ip-address <IP> --end-ip-address <IP>
> ```
> (Note: this `az` version uses `-s` for the server and `-n` for the rule name.)

## 3. Get the secrets from the owner (out-of-band — NOT via git/chat)
Ask the owner to send you their two env files securely (password manager / Signal / etc.):
- **`pivot/.env`** — drop it in `pivot/.env`. It contains the shared Azure DB DSNs,
  the LLM keys, and the Kite keys. **Use it as-is** — in particular `KITE_TOKEN_ENC_KEY`
  *must* match the owner's, or the backend can't decrypt the shared Kite tokens.
- **`pivot-next/.env.local`** — or just create it yourself pointing at *your* local backend:
  ```
  NEXT_PUBLIC_PIVOT_API_BASE=http://localhost:8000
  NEXT_PUBLIC_PIVOT_WS_BASE=ws://localhost:8000
  ```

## 4. Backend
```bash
cd pivot
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# DO NOT run migrations — the shared Azure DB is already created + populated.
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
Verify: `curl -s localhost:8000/health` → should show `"database":"ok"`.

## 5. Frontend
```bash
cd pivot-next
pnpm install
pnpm dev        # http://localhost:3000
```

## 6. Sanity check
Open http://localhost:3000, send a chat like *"analyse RELIANCE"*. A structured
read with live numbers means you're wired to the shared DB + Kite.

---

## Things to know
- **Shared state:** you and the owner hit the *same* `pivot_db` + `financials`, so
  users, workflows, paper trades, chat history, and `llm_usage` are all shared. Great
  for collaboration; just know your writes are visible to each other.
- **Kite:** analysis/technicals/history pull **live Kite** automatically (the token is
  resolved globally from the shared DB). Live *spot* quotes are per-user, so if you
  haven't authed your own Kite they fall back to yfinance — harmless for dev. The
  daily Kite token expires ~6 AM IST; whoever re-auths refreshes it for everyone's
  analysis path.
- **Latency:** the DB is in **Central India**. If you're far from there, expect higher
  per-turn latency (it's RTT-bound). Run `pivot/.venv/bin/python scripts/latency_bench.py mine`
  to measure your own.
- **Don't commit secrets:** `.env`, `.env.local`, `.pgpass` are all gitignored — keep it that way.
