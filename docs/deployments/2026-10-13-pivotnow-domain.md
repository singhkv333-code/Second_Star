# Pivot domain integration — 13 October 2026

## Approved routing

The owner chose to keep the landing page on Vercel and the platform on Azure.
DNS remains with Hostinger; changing nameservers would also affect email.

| Address | Hosting | Purpose |
|---|---|---|
| pivotnow.in | Vercel | Redirect to www.pivotnow.in |
| www.pivotnow.in | Vercel, project pivotnow | Public landing page, legal pages and waitlist |
| app.pivotnow.in | Azure pivot-clean-stage, 20.204.109.233 | Existing platform, chart, API and auth |
| pivot-india.centralindia.cloudapp.azure.com | Same Azure host | Retained working fallback |

Only app A=20.204.109.233, TTL=300, was added to the authoritative DNS.
Root A=216.198.79.1, www CNAME=pivotnow.in, MX/SPF/DKIM/DMARC remain intact.
A one-use _acme-challenge.app TXT was added for certificate issuance.

## Infrastructure changes

- app TLS was issued using the VM's existing ACME account before adding the app A record.
- app certificate expires 11 January 2027. The renewal configuration now uses
  webroot HTTP-01 at /var/www/pivot-acme; simulated renewal succeeded.
- Existing certbot.timer is active. A domain-specific nginx reload hook is
  /etc/letsencrypt/renewal-hooks/deploy/30-pivot-domain-nginx.
- /etc/nginx/sites-available/pivotnow-app and its sites-enabled symlink reuse
  the first TLS server's actual routes from /etc/nginx/sites-available/pivot-clean.
  Its certificate path is set explicitly; the old Certbot lineage name must
  never be inferred from server_name.
- app HTTP redirects to a fixed HTTPS hostname. Initial HSTS is one day,
  without includeSubDomains or preload. /terms, /privacy and /about redirect
  to the public Vercel legal pages; these pages need no authentication.
- Whole nginx backup before the final change:
  /root/pivot-domain-cutover/nginx-before-20261013T130614Z/nginx.
  Initial app activation backup (before adding the app vhost):
  /root/pivot-domain-cutover/nginx-before-20261013T125224Z/nginx.
- /root/pivot-domain-cutover/published.json records checks and backup location.

No database migration, new user database, credential rotation, application
service restart or firewall expansion was needed. The old VM's writers must
remain stopped. Do not move the IP or restart old writers as a domain rollback.

## Landing release

Deployment is prepared in /tmp/pivot-landing-domain-release-20261013. Secrets
were excluded from the source copy; production build settings were pulled via
Vercel CLI without printing their values. Existing landing components were
reconstructed from ea6674028702045893c8ecbdb70407a90b18ecf7 and the existing
Checkout footer link retained. The homepage addition is Open Pivot, linking to
https://app.pivotnow.in/. The newer local v3 redesign was excluded.

The Vercel project was verified as prj_KDAYxkWZqEYasORE6oyTxdLQXbxw in
singhkv333-codes-projects. Build with --prod --standalone, deploy --prebuilt
--prod --skip-domain --archive=tgz, verify, then publish the tested deployment.
The original www alias deployment was dpl_6gEsMKM546Lf1bsVh14LwvV76ZgZ
(pivotnow-mcd6ohnzf-singhkv333-codes-projects.vercel.app).
Public domains were assigned to the older project pivot despite their live
alias targeting pivotnow; reconcile assignment with the actual serving project
before relying on automatic future deployment routing. This was reconciled
using Vercel's domain-move API: both public names now belong to pivotnow,
with the apex-to-www 307 redirect preserved. Final deployment:
dpl_DxmAmyePeb3un3DFHZodNn8CmxkN
(pivotnow-9477x6pt4-singhkv333-codes-projects.vercel.app).

## Verification and limits

Verified valid TLS without insecure certificate flags; HTTP-to-HTTPS redirect;
old and new host readiness; fresh whole-state backup; main, login and chart HTML;
signed-out /auth/me matching the old host's 200 {user:null} contract; data API;
visible RELIANCE candles and volume in a guest browser with no console errors;
checkout HTML; and an SSE endpoint returning text/event-stream. No payment,
chat/model call, real-account signup, broker order or credential submission was
made. This is not proof of current live market ticks or successful paid checkout.
Four offline domain-routing fixtures pass. Reload failures restore the prior
nginx configuration before exiting; probes caught certificate-lineage and auth
contract assumptions during preparation, before the app A record was published.

A new browser origin requires one normal email/password sign-in. Existing
accounts and saved state are retained. The owner explicitly deferred Google
sign-in configuration; app origin registration with Google is not verified.

The pre-existing PostgreSQL allow-all-dev rule is still 0.0.0.0–255.255.255.255.
Do not call the entire platform hardened or fully production-ready. Inventory
VM, Vercel waitlist and other clients before narrowing the shared firewall;
replace serverless direct DB access with an authenticated controlled path or
private connectivity as appropriate. Previously documented shared-credential
revocations and live-feed boundaries are separate unresolved work.

## Rollback

1. Landing only: reassign www.pivotnow.in to the saved original deployment
   using Vercel alias set. Preserve the apex-to-www redirect. No DNS or app
   state rollback is needed.
2. Domain routing: restore only the backed-up pivotnow-app site/symlink,
   nginx -t, then gracefully reload. Keep the public A record while restoring
   a working known configuration, so existing app users retain HTTPS.
3. If retiring the app address, remove the landing link first and arrange a
   valid HTTPS redirect to the retained Azure address before DNS removal.
   Browser localStorage is origin-specific; do not copy bearer tokens in URLs.
4. Never restore an older user DB to undo a URL change; that would discard
   writes accepted since cutover. Certbot renewal and state backup timers remain
   active independently of domain routing rollback.

Official references:
- https://vercel.com/docs/cli
- https://vercel.com/docs/domains/working-with-dns
- https://letsencrypt.org/docs/challenge-types/

Public waitlist validation was checked with an empty request (HTTP 400); no waitlist row or mail was created. Public build assets were scanned against configured private environment values; no private secret was found. No Git push was performed.

Final browser check: the published Open Pivot link reaches https://app.pivotnow.in/login with the email/password form. app /terms and /privacy both redirect over HTTPS to public pages returning HTTP 200. Vercel domain inspection confirms both root and www on the correct pivotnow project. Source homepage text is identical to the previous production homepage after excluding the added Open Pivot label.

Future platform releases that change nginx routes must regenerate the app vhost from the updated pivot-clean routes with publish_app_domain.py, then rerun probes. The initial app vhost does not automatically follow later routing changes.

## Landing v3 release — 13 October 2026, evening

www.pivotnow.in now serves the v3 landing (commit 3d6dc9b9 plus 9dff2f2f and the
uncommitted Open Pivot nav link). The hero's "Get early access" button became
"Open platform", linking to https://app.pivotnow.in/; "Join waitlist" stays in
the nav and the waitlist section is unchanged.

- Release copy: /tmp/pivot-landing-v3-release-20261013 (the morning copy, with
  the current charto/web source synced over it; env pulled earlier, not printed).
- Built with vercel build --prod --standalone; deployed --prebuilt --prod
  --skip-domain --archive=tgz; checked locally at desktop width; then promoted.
- Deployment: dpl_U7wsoouCsbCV6PEscybp5U3znodY
  (pivotnow-h9v4ab78l-singhkv333-codes-projects.vercel.app).
- Verified live: apex 307 to www; v3 copy and Open platform present; /terms,
  /privacy, /about, /checkout 200; empty waitlist POST 400; app.pivotnow.in 200.
  The static output was scanned for the private env values: no hits.
- Rollback: vercel promote pivotnow-9477x6pt4-singhkv333-codes-projects.vercel.app
  (dpl_DxmAmyePeb3un3DFHZodNn8CmxkN, the morning release).
