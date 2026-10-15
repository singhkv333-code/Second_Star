# Kite login procedure (market-data feed)

Pivot's live NSE/BSE data comes from one Zerodha account, **NHB457** — the
pinned `kite_data_account` (default in `pivot/backend/config.py`, overridable
with a `KITE_DATA_ACCOUNT` env var). Kite access tokens expire around **6 AM IST
every day**, so this account is logged in once each morning before 09:15.
Zerodha requires that login to be done by the account holder; it is not
automated on purpose.

## One-time setup

In the Kite developer console (developers.kite.trade → the Pivot app,
api_key `4mw0xc4105d5rbs3`), set the **Redirect URL** to:

```
https://pivot-india.centralindia.cloudapp.azure.com/pv/callback
```

The old value pointed at the site root, which dropped the `request_token`.
This one URL serves both flows: users' "Connect Kite" logins (they carry a
signed `state`) and the morning data-feed login below (no `state`).

## Every morning (about 20 seconds)

1. Open the login link (bookmark it):

   ```
   https://kite.zerodha.com/connect/login?api_key=4mw0xc4105d5rbs3&v=3
   ```

2. Log in as **NHB457** and enter the TOTP from the authenticator app, or
   generate one locally from the seed in `pivot/.env` (prints only the code):

   ```
   cd pivot && python3 -c "import re,time,hmac,hashlib,base64,struct;s=re.search(r'^KITE_PERMANENT_TOKEN=[\"\']?([^\"\'\s#]+)',open('.env').read(),re.M).group(1).upper();k=base64.b32decode(s+'='*(-len(s)%8));t=int(time.time());h=hmac.new(k,struct.pack('>Q',t//30),hashlib.sha1).digest();o=h[-1]&15;print('%06d'%((struct.unpack('>I',h[o:o+4])[0]&0x7FFFFFFF)%10**6),'| valid',30-t%30,'s')"
   ```

3. Kite redirects to `/pv/callback`, and the server does the rest: it
   exchanges the token, stores the session for NHB457 and re-arms charto's Kite
   stream. The page reads **"Market data connected — Kite account NHB457 is
   connected for today. Live feed: re-armed."**

That page is the confirmation. Nothing else is needed.

### If the page says something else

| Page says | Meaning | Do |
|---|---|---|
| Kite rejected the login token | The `request_token` expired (they last minutes) or was already used | Open the login link again |
| Not the platform's market-data account | Logged in with a Zerodha id other than NHB457 | Log out of Kite, log in as NHB457 |
| Live feed: not re-armed … | The session is stored but charto's stream did not restart | Re-arm by hand (below) |

## Checking the feed

```
curl -s "https://app.pivotnow.in/bars?symbol=RELIANCE&interval=1m&limit=1"
```

During market hours the bar's `t` should be within a minute or two of now.
Operator view of every stream (run on the VM):

```
curl -s "http://127.0.0.1:5174/live?status=1"
```

## Manual fallback (VM, via `az vm run-command`)

The VM's ssh is closed; commands run as root through
`az vm run-command invoke -g PIVOT -n pivot-clean-stage --command-id RunShellScript --scripts '...'`.

Re-arm the Kite stream (reads the session already stored):

```
K=$(sed -n 's/^CHARTO_FEED_CONTROL_KEY=//p' /etc/pivot/runtime.env | tr -d "\"'")
curl -s -X POST http://127.0.0.1:5174/live -H "Content-Type: application/json" -H "X-Pivot-Feed-Key: $K" -d '{"venue":"kite","stop":true}'
curl -s -X POST http://127.0.0.1:5174/live -H "Content-Type: application/json" -H "X-Pivot-Feed-Key: $K" -d '{"venue":"kite","symbols":[]}'
```

Exchange a `request_token` by hand (if the callback is unreachable): copy the
token from the address bar after the Kite login and, within a few minutes, run
`backend.kite.auth.exchange_request_token(<token>)` then
`backend.brokers.sessions.upsert_broker_session(...)` from
`$(cat /etc/pivot/stage-release)/pivot` with `/etc/pivot/runtime.env` loaded,
as done on 2026-10-15.

## After a long outage

If the feed has been down for more than a few days, the stream's freshness
gate refuses to open over the hole. Fill it first, detached:

```
systemd-run --unit=pivot-kite-catchup --uid=pivot-data --gid=pivot-runtime \
  -p EnvironmentFile=/etc/pivot/runtime.env -p Nice=10 \
  /opt/pivot/venv/bin/python -u /var/lib/pivot-catchup/catch_up.py
```

(`/var/lib/pivot-catchup/catch_up.py` is `charto/deploy/catch_up_clean_universe.py`
with the release path baked in, because `pivot-data` cannot read
`/etc/pivot/stage-release`.) Progress: `journalctl -u pivot-kite-catchup`.
Crypto has the same gate: if its daily series falls behind, refold `bars_1d`
from the minutes before re-arming bybit/coinbase.

## Where things live

- Callback and data-feed login: `pivot/backend/routers/brokers.py`
  (`_complete_data_feed_login`, `_rearm_charto_kite_feed`).
- The pinned account: `settings.kite_data_account`, default `NHB457` in
  `pivot/backend/config.py` (env `KITE_DATA_ACCOUNT` overrides).
- Venues armed at charto boot: `CHARTO_LIVE_VENUES=bybit,coinbase,kite` in
  `/etc/pivot/runtime.env`.
- Charto reads the token from the stored session
  (`charto/data/kite_stream.py::_kite_access_token`).
