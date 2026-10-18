# No email confirmation; the welcome goes out at sign-up; black-and-white mail

**Status: written and tested locally, uncommitted, not pushed, not deployed.**

## Sign-up and sign-in

- `charto/data/dataserver.py` `_auth_signup` no longer sends a confirmation link and
  no longer returns `verification_sent`. Sign-in never checked confirmation, so
  nothing else gated on it.
- `charto/data/notify.py` `_signed_in` welcomes every new account at once, email or
  Google. The welcome is keyed `welcome/1` in `mail_sent`, so an old confirmation link
  clicked later (`/auth/verify` → `confirmed_email`) does not send a second one.
- Email sign-ups are still recorded as unconfirmed (`users.email_verified` NULL). That is
  deliberate: it is what lets a Google sign-in by the inbox's real owner take back an
  account someone else opened in their name (`_auth_google`).
- The shell drops the "Confirm your email" toast (`AppShell.tsx`, `signup/page.tsx`) and
  the "Not confirmed yet / Send link" row in Settings. `/verify-email` and
  `/auth/verify` stay, so links already sent still open to an answer.
  `/auth/verify/resend` is now unused by the app.

## Mail design (`charto/data/mail_kit.py`, every message)

- Ink, greys and white; no cyan or amber. Links are ink, underlined.
- Every message opens on a masthead: the sign-in panel's wave field with grain and
  vignette (`charto/preview/assets/email/masthead.jpg`, 1200x560) and the wordmark,
  the alien inside "Pivot", in white (`wordmark-white.png`, 480x181, from
  `assets/pivot-logo.svg`). A `hero()` headline (welcome, access, new plan) is set on
  the masthead under the wordmark.
- Images load from `https://app.pivotnow.in/assets/email/`. **They must be deployed
  before mail goes out**, or the masthead shows as a plain black band with "Pivot" as
  alt text. Outlook for Windows ignores CSS background images and shows the black band
  without texture.

## Tests

`test_mail_templates.py` (56), `test_notify.py`, `test_auth_guard.py`,
`test_entitlements_api.py`, `test_shares.py` pass.
`test_analytics.py::test_signup_and_login_are_captured` fails because its fake handler
has no `headers`, which `_client_ip` reads; that code path is not part of this change.
