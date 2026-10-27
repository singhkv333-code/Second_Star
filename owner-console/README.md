# Owner console

The private desk for whoever runs Pivot. You use it to make codes, look up an account, grant or revoke a plan, and see the business at a glance.

- **Live:** https://pivot-owner-847d37.azurewebsites.net (Azure App Service, resource group `pivot-ops`, Central India)
- **Passphrase:** `~/.config/pivot-ops/password` on the owner's laptop. Copy it with `pbcopy < ~/.config/pivot-ops/password`. Azure holds only its scrypt hash. See [PASSPHRASE.md](PASSPHRASE.md).

## What it does

| Section | What you can do |
|---|---|
| Overview | See accounts and sign-ups (30-day chart), weekly actives, paying subscribers, monthly recurring revenue, money collected in the last 30 days, grants in force, code use, and webhooks that need a look. |
| Codes | Make one code or a batch of up to 1,000. You can set limits, change them, disable or archive a code, disable a whole batch, export a CSV, copy a checkout link, see who used a code, and revoke free time. |
| Accounts | Search by email, name or account number. You can see an account's plan, subscription, sessions, grants, codes and usage. You can also grant a plan or a single limit (for example 500 AI credits for 30 days) and sign the account out everywhere. |
| Grants | Every comp, code and deal in force. You can revoke any of them. |
| Subscriptions | Every subscription row. Refunds and cancellations still happen in the Razorpay dashboard. |
| Webhooks | The latest 200 Razorpay events and what billing did with each one. |
| Audit log | Every change made from the console, with the address it came from. |
| System | The server's own health report. |

## Codes

**Free time** (`access`): for example "Pro free for 30 days".
- The user redeems it in the app with no payment, either on the checkout page or under Plan & billing → Redeem a code.
- It becomes a grant with an end date, and the plan lapses by itself when the days run out.

**Discount**: for example "40% off Pro", or "₹200 off Pro+ yearly".
- The user applies it at checkout. The first checkout with a code creates a Razorpay plan at that price, and the subscription runs on that plan.
- **Billing cycles at this price** is optional:
  - Empty: every renewal stays at the lower price.
  - A number N: the subscription is authorised for N cycles at the lower price. It then ends, and the customer renews at full price.

**Limits on every code:**
- total uses across everyone
- uses per account
- start and end dates
- only these emails (for specific people)
- only one email domain
- only accounts that have never subscribed

**What can't change:** a code's price terms (plan, days, percent or amount, cycles) are fixed once it exists, because a Razorpay plan may already carry them. Disable the code and make a new one instead.

**Holding a use during checkout:** when a discount checkout starts, one use is held for it. The hold becomes "redeemed" once the payment is confirmed. It is released if the subscription dies unpaid, and it stops counting after 48 hours.

**Link a customer can open:** `https://app.pivotnow.in/checkout?plan=pro&code=LAUNCH40`. The console's "Copy checkout link" builds this. The same link works for free-time codes.

## From a terminal

```sh
owner-console/pivot_codes.py create --kind access --plan pro --days 30 --emails ravi@example.in
owner-console/pivot_codes.py create --kind discount --plan pro --percent 40 --discount-cycles 3 --code LAUNCH40
owner-console/pivot_codes.py create --kind access --plan pro_plus --days 14 --count 50 --prefix CREATOR --max 50
owner-console/pivot_codes.py list --status active
owner-console/pivot_codes.py disable LAUNCH40
owner-console/pivot_codes.py export > codes.csv
```

The script signs in through the console's own sign-in, with the same lockout. It reads the console address from `~/.config/pivot-ops/console-url` and the passphrase from `~/.config/pivot-ops/password`.

There is also `charto/data/coupons.py`, which works directly against an account database. It needs no network and is useful locally or on the VM:

```sh
python3 coupons.py charto_users.db create --kind access --plan pro --days 30
```

## How it is kept safe

Browser → console:
- **Passphrase:** one passphrase of about 428 bits, checked against scrypt (N=2^15).
- **Failed attempts:** five wrong attempts lock that address for 15 minutes. Every failure costs about 1 second, and sign-ins are checked one at a time.
- **Session:**
  - It is a 256-bit token in a `__Host-` cookie (Secure, HttpOnly, SameSite=Strict, no Domain), and the server keeps only a hash of it.
  - It is bound to the browser's User-Agent.
  - It ends after 30 minutes idle or 8 hours, and also on sign-out or restart.
- **Writes:** every write needs the session's CSRF token in a header and an Origin that matches the console.
- **Response headers:**
  - Content-Security-Policy `default-src 'none'`: scripts and styles come only from the console itself, with no inline code.
  - HSTS for two years.
  - `X-Frame-Options: DENY` and `noindex`.
  - Nothing is cached.
- **Hostnames:** only the console's own hostname is served.
- **Forwarding:** only an allow list of routes and query keys is passed on to the server.

Console → Pivot server:
- **Signature:** every request carries an HMAC-SHA256 over method, path, timestamp, nonce and body hash (`charto/data/ops_api.py`).
- **Replays:** the clock must agree within ±60 s, and each nonce is accepted only once.
- **Addresses:** requests must come from the console's outbound addresses (`PIVOT_OPS_ALLOWED_IPS`).
- **Refusals:** every refusal is the same 404, and without the key the routes do not exist.
- **What the API can't do:** move money, cancel a provider subscription, or read passwords, sessions, charts or chats.

Azure:
- **Transport:** HTTPS only, TLS 1.3 minimum, HTTP/2.
- **Deploy access:**
  - FTP and FTPS are off.
  - Basic-auth publishing (FTP and SCM) is off, so deploys use Entra tokens.
  - The deploy endpoint accepts only the owner's address. Run `./deploy_azure.sh allow-me` after the address changes.
- **Secrets:** the passphrase hash and the HMAC key sit in Key Vault `pivot-ops-kv-847d37` (RBAC, purge protection). The app reads them through its managed identity.
- **Logs:** the access log records method, path, status and size, never query strings.

## Operating it

```sh
./deploy_azure.sh deploy       # ship app.py + static/
./deploy_azure.sh allow-me     # new laptop address, before deploying
./deploy_azure.sh vm-secret    # (production vault) the key + addresses the VM reads
```

**New passphrase:**
See [PASSPHRASE.md](PASSPHRASE.md): what the passphrase is, where it lives, and the exact steps to change it.

**On the VM** (`charto/deploy/activate_owner_console.py`, run through `az vm run-command`):
- `stage` writes `/etc/pivot/owner-console.env` and a charto drop-in, then waits for the next restart.
- `verify` is read-only.
- `remove` is the rollback.

**Tests:**
- `cd owner-console && python3 -m pytest -q test_console.py`
- `cd charto/data && PAYWALL_ENABLED=1 ../../pivot/.venv/bin/python -m pytest -q test_coupons.py`. This includes a real console-to-dataserver round trip.
