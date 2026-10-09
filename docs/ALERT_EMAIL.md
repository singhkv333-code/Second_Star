# Triggered-alert email

Chart alerts created by the dialog or chat now enqueue transactional email
to the owning account's `users.email`, from **info@pivotnow.in**. Existing
alerts default to email on for **future triggers**; historical logs are not
bulk emailed. Each alert has an Email On/Off control. No arbitrary recipient
is accepted from the browser or model. The alert log shows delivery status.

## Google Workspace configuration

Use `smtp.gmail.com`, port 465 (verified TLS) or 587 (mandatory STARTTLS), with
the full mailbox address and a Google **app password**, not its normal login
password. Google requires 2-Step Verification for app passwords; Workspace
admin policies may prohibit them. In that case use an administrator-approved
mail relay or OAuth integration; those transports are not implemented here.
If `info@` is an alias, authenticate its owning mailbox and confirm `info@`
is an approved send-as address before enabling delivery.

References: [Google SMTP configuration](https://support.google.com/a/answer/176600)
and [app-password requirements](https://support.google.com/accounts/answer/185833).
Configure the domain's SPF/DKIM/DMARC in Google Workspace to improve delivery.
This implementation does not change DNS or Google account settings.

The clean serving VM is `PIVOT/pivot-clean-stage`. Store the app password in
`/etc/pivot/alert-smtp-password`, root-owned, group `pivot-data`, mode `0640`;
never in Git, browser code, chat, a shell argument or a log. Copy the non-secret
fields from [the example](../charto/deploy/alerts-mail.env.example) to
`/etc/pivot/alerts-mail.env` with the same owner/group/mode. Add this systemd
drop-in for the `charto` service:

```ini
# /etc/systemd/system/charto.service.d/alerts-mail.conf
[Service]
EnvironmentFile=/etc/pivot/alerts-mail.env
```

Reload systemd and restart **only charto** once the source files are installed.
Do not restart the broker feed unnecessarily or touch the rollback VM.
If the account is not yet configured, triggers remain in-app and emails are
pending, never falsely marked sent. The dialog reports sender unavailability.
An environment-only `CHARTO_SMTP_PASSWORD` is also supported for a secret
manager that injects process environment, but the private file is preferred.

The scoped deployment helper `charto/deploy/deploy_alert_email.py` reuses the
existing private-blob transport, checks original source hashes, preserves a
whole-plane SQLite backup and updates only this feature's files/contexts.
It restarts charto, checks readiness and restores source on failure. It does
**not** enable SMTP or push Git. The Azure managed-command name it prints is
dispatch confirmation only; check its `instanceView.executionState` and output
for successful execution before claiming deployment.

## Delivery and reliability

- `alert_email_outbox` lives in the existing **whole user-state SQLite DB**
  and is included in its regular backups. Queue insert, fire log and alert
  state update commit together. Do not migrate or delete the user DB.
- A separate daemon worker wakes immediately after a trigger, or within five
  seconds for a recovered queue. SMTP has a 15-second timeout and never holds
  the user DB lock. Real latency depends on Google and the destination inbox.
- A unique `log_id` prevents multiple queue jobs for one fire. Stable Message-ID
  aids tracing. Sent means **SMTP accepted**, not proof of inbox delivery.
- Temporary explicit rejections and pre-send connection failures back off
  from 5 seconds to 5 minutes, up to six attempts. Authentication failures
  pause transport for five minutes rather than hammering Google.
- SMTP cannot guarantee exactly once. Lost responses after submission, or
  interrupted in-flight jobs older than two minutes, become **uncertain** and
  are not automatically resent. Inspect before any manual resend.
- A conservative 1,000 accepted messages per rolling 24 hours prevents
  consuming the full Workspace mailbox quota. Other mailbox consumers can
  still cause rejection; Google limits apply. A 60-mail/hour/account cap
  delays noisy accounts without blocking other users. Messages waiting over 24 hours
  expire instead of sending stale alert floods. Terminal records are kept
  30 days; the ordinary alert log retains its existing 500-entry limit.
- Disabling email or deleting an alert cancels its pending jobs, but cannot
  recall a mail already in flight. Changed/deleted accounts cancel jobs for
  the old email address. Catch-up triggers explicitly show their historical
  trigger time and that they were detected late.
- Alert email does not place trades, and includes the analysis disclaimer.

## Validate / rollback

Run the isolated fixture suite (no network mail and no customer-state writes):

```sh
PYTHONPATH=charto/data pivot/.venv/bin/python -m pytest -q \
  charto/data/test_alerts.py charto/data/test_alert_email.py
node --check charto/preview/js/alerts.js
node --check charto/preview/js/panels.js
```

For live validation, use an explicitly approved test account, create a real
crossing alert, and check the alert-log status plus the recipient inbox. Do
not manufacture a price crossing or email every account as a test. Never
claim live validation without a genuine connected market feed.

To disable mail immediately, set `CHARTO_ALERT_EMAIL_ENABLED=false` and restart
charto. In-app alert evaluation and durable logs remain active. To roll back
source, restore the exact saved source files and JS version stamps; the new
outbox table is additive and should be left intact. Preserve a fresh entire
user DB backup before a VM rollback so no newer user state is lost.
