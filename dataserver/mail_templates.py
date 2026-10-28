"""Every email Pivot sends, written once, in one voice.

Each function returns a `Mail`: subject, preheader, HTML and plain text. The
caller supplies the facts (amounts, dates, levels) already formatted, and the
template prints them exactly as given; a template never computes or invents a
number. Links are built from `origin`, the public address of the app.

Voice: calm and plain. Say what happened, what it means and what to do next,
in that order. No exclamation marks, no jargon, no internal names.

Categories decide what a reader can opt out of:
  security  password, sign-in and incident notices; always sent
  account   welcome, waitlist, data requests
  billing   receipts and renewals; always sent while a plan is active
  product   alerts, summaries, strategy fills; each has its own off switch
  service   policy changes and outages that affect the reader
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

from mail_kit import (Block, Footer, Link, bullets, button, code, divider, facts,
                      heading, hero, note, page, para, steps, subheading, table)

CONTACT = "contact@pivotnow.in"
APP = "https://app.pivotnow.in"
SITE = "https://www.pivotnow.in"


@dataclass(frozen=True)
class Mail:
    kind: str
    category: str
    subject: str
    preheader: str
    html: str
    text: str


def _subject(text: str) -> str:
    # A subject is a header: no line breaks or control characters, ever.
    return re.sub(r"[\r\n\x00-\x1f\x7f]", " ", str(text)).strip()[:150]


def _mail(kind, category, subject, preheader, blocks: Sequence[Block], footer: Footer,
          sender: str = CONTACT) -> Mail:
    subject = _subject(subject)
    html, text = page(subject=subject, preheader=preheader, blocks=blocks,
                      footer=footer, sender=sender)
    return Mail(kind, category, subject, preheader, html, text)


def _first(name: str | None) -> str:
    name = str(name or "").strip()
    return name.split()[0] if name else ""


def _hi(name: str | None) -> str:
    first = _first(name)
    return f"Hi {first}," if first else "Hello,"


ACCOUNT_REASON = "You're receiving this because you have a Pivot account."
SECURITY_REASON = ("This is a security notice for your Pivot account. "
                   "We send these even if you have turned other emails off.")
BILLING_REASON = "You're receiving this because you have a paid Pivot plan."


# ── account and security ────────────────────────────────────────────────────

def verify_email(*, email: str, url: str, name: str | None = None,
                 hours: int = 24, origin: str = APP) -> Mail:
    return _mail("verify_email", "account", "Confirm your email for Pivot",
                 "One click confirms this address is yours.", [
        heading("Confirm your email"),
        para(_hi(name)),
        para(f"Please confirm that {email} is your email address. It lets you "
             "recover your account and lets your alerts reach you."),
        button("Confirm email", url),
        para(f"The link works for {hours} hours. If the button does not open, "
             "copy this address into your browser:"),
        para(Link(url, url)),
        note("If you did not create a Pivot account, you can ignore this email. "
             "Nothing will be set up without your confirmation."),
    ], Footer("You're receiving this because this address was used to sign up for Pivot."))


def welcome(*, name: str | None = None, origin: str = APP) -> Mail:
    first = _first(name)
    return _mail("welcome", "account", "Welcome to Pivot",
                 "Charts, company research and alerts, with answers you can check.", [
        hero(f"Welcome to Pivot, {first}" if first else "Welcome to Pivot",
             "A chart that explains itself, with evidence you can check."),
        para("Pivot brings charts, company research and market alerts into one "
             "workspace, with an assistant that answers from real market data "
             "and shows the evidence behind every answer."),
        subheading("Three good places to start"),
        steps([
            ("Ask a question on a chart",
             "Try “Where are the key levels on RELIANCE?” The answer is "
             "drawn on the chart, with the evidence for each level."),
            ("Read a company in depth",
             "Open any listed company for its quarterly results, ratios, "
             "shareholding and annual-report filings, side by side."),
            ("Let an alert watch for you",
             "Set a price or indicator alert and Pivot emails you when it triggers, "
             "even with the app closed."),
        ]),
        button("Open Pivot", origin),
        para("Pivot covers NSE and BSE equities, indices, F&O and MCX commodities. "
             "It is a research tool: it never places orders with a broker."),
    ], Footer("You're receiving this because you created a Pivot account.", market=True))


def password_reset(*, email: str, url: str, minutes: int = 30,
                   origin: str = APP) -> Mail:
    return _mail("password_reset", "security", "Reset your Pivot password",
                 f"This link expires in {minutes} minutes.", [
        heading("Reset your password"),
        para(f"We received a request to reset the password for {email}. "
             "Choose a new one with the button below."),
        button("Choose a new password", url),
        para(f"The link expires in {minutes} minutes and works once. "
             "If the button does not open, copy this address into your browser:"),
        para(Link(url, url)),
        note("If you did not ask for this, you can ignore this email. Your password "
             "stays the same, and no one can change it without this link."),
    ], Footer(SECURITY_REASON))


def password_changed(*, email: str, when: str, origin: str = APP) -> Mail:
    return _mail("password_changed", "security", "Your Pivot password was changed",
                 "If this was you, there is nothing more to do.", [
        heading("Your password was changed"),
        para(f"The password for {email} was changed on {when}. "
             "For your safety, every other device was signed out."),
        para("If this was you, there is nothing more to do."),
        note("If you did not change it, reset your password straight away with ",
             Link("this link", f"{origin}/reset-password"),
             " and reply to this email so we can secure your account."),
    ], Footer(SECURITY_REASON))


def new_sign_in(*, email: str, when: str, device: str,
                location: str | None = None, origin: str = APP) -> Mail:
    return _mail("new_sign_in", "security", "New sign-in to your Pivot account",
                 f"A new device signed in on {when}.", [
        heading("New sign-in to your account"),
        para(f"Your account {email} was just used to sign in on a device "
             "we haven't seen before."),
        facts([("Time", when), ("Device", device), ("Approximate location", location)]),
        para("If this was you, there is nothing more to do."),
        note("If you don't recognise this sign-in, ",
             Link("reset your password", f"{origin}/reset-password"),
             " now. That signs out every device, including this one."),
    ], Footer(SECURITY_REASON))


def email_changed(*, old_email: str, new_email: str, when: str,
                  origin: str = APP) -> Mail:
    return _mail("email_changed", "security", "Your Pivot sign-in email was changed",
                 f"Your account now signs in with {new_email}.", [
        heading("Your sign-in email was changed"),
        para(f"On {when}, the email address for your Pivot account was changed "
             f"from {old_email} to {new_email}. This is the last message we will "
             "send to this address."),
        note("If you did not make this change, reply to this email straight away. "
             "We will hold the account while we confirm who it belongs to."),
    ], Footer(SECURITY_REASON))


def account_closed(*, email: str, when: str, origin: str = APP) -> Mail:
    return _mail("account_closed", "account", "Your Pivot account has been closed",
                 "Your saved work has been deleted, as you asked.", [
        heading("Your account has been closed"),
        para(f"As you asked, we closed the Pivot account for {email} on {when}."),
        para("Your profile, conversations, layouts, drawings, alerts, notes and "
             "paper trades have been deleted. Older copies remain only in our "
             "encrypted backups, which are never used to restore a closed account."),
        para("We keep payment and invoice records for as long as tax law requires, "
             "and security logs for up to 180 days. They are used for nothing else."),
        para("You are welcome back anytime. A new account starts fresh."),
    ], Footer("You're receiving this because you asked us to close your Pivot account."))


def data_export_ready(*, url: str, days: int = 7, origin: str = APP) -> Mail:
    return _mail("data_export_ready", "account", "Your Pivot data is ready to download",
                 f"The download link works for {days} days.", [
        heading("Your data is ready"),
        para("Here is the copy of your personal data you asked for: your profile, "
             "conversations, saved layouts, drawings, alerts, notes and paper trades."),
        button("Download your data", url),
        para(f"The link works for {days} days."),
        note("If you did not request this, reply to this email and we will "
             "cancel the download."),
    ], Footer("You're receiving this because you asked for a copy of your data."))


# ── the waitlist ────────────────────────────────────────────────────────────

def waitlist_joined(*, email: str, origin: str = APP) -> Mail:
    return _mail("waitlist_joined", "account", "You're on the Pivot waitlist",
                 "We'll email you as soon as your access is ready.", [
        heading("You're on the list"),
        para("Thank you for your interest in Pivot. We are opening access in small "
             "groups, so everyone who joins gets a product that works well and a "
             "team that answers quickly."),
        para(f"We will write to {email} when your access is ready. "
             "Until then, there is nothing you need to do."),
        para("While you wait, our ", Link("about page", f"{SITE}/about"),
             " explains what Pivot does and how it handles your data."),
    ], Footer("You're receiving this because you joined the Pivot waitlist at pivotnow.in."))


def access_granted(*, email: str, origin: str = APP) -> Mail:
    return _mail("access_granted", "account", "Your Pivot access is ready",
                 "Create your account and start right away.", [
        hero("Your access is ready", "Your place on the waitlist has come up."),
        para(f"Create your account with {email} and you can start straight away."),
        button("Create your account", f"{origin}/signup"),
        para("Your first question can be as simple as naming a stock. Pivot opens "
             "its chart, reads it, and marks what matters."),
    ], Footer("You're receiving this because you joined the Pivot waitlist at pivotnow.in.",
              market=True))


# ── product ─────────────────────────────────────────────────────────────────

def alert_triggered(*, symbol: str, condition: str, value: str, level: str,
                    interval: str, when: str, alert_id: int, verb: str = "",
                    note_text: str = "", late: bool = False,
                    origin: str = APP) -> Mail:
    title = f"{symbol} {verb} {level}".strip() if verb and level else f"{symbol}: {condition}"
    blocks = [
        heading(title),
        para(f"Your alert on {symbol} triggered at {when}."),
        facts([("Condition", condition), ("Last value", value), ("Level", level),
               ("Timeframe", interval), ("Triggered at", when)]),
    ]
    if note_text:
        blocks.append(para(f"Your note: “{note_text}”"))
    if late:
        blocks.append(note("Pivot was reconnecting to the market feed when this "
                           "happened, so this email is late. The time above is "
                           "when the condition was actually met."))
    blocks.append(button("Open the chart", f"{origin}/#chart"))
    return _mail("alert_triggered", "product", f"Alert: {title}",
                 f"{condition}. Last value {value}.", blocks,
                 Footer(f"You're receiving this because email is on for alert #{alert_id}. "
                        "You can turn it off in that alert's settings in Pivot.",
                        market=True))


def alerts_expiring(*, alerts: Sequence[tuple[str, str, str]], origin: str = APP) -> Mail:
    n = len(alerts)
    what = "1 alert expires" if n == 1 else f"{n} alerts expire"
    return _mail("alerts_expiring", "product", f"{what} this week",
                 "Extend the ones you still need.", [
        heading(f"{what} this week"),
        para("Alerts expire after a set time so old ones don't keep firing. "
             "These will stop watching soon:"),
        table(["Instrument", "Condition", "Expires"], alerts,
              ["left", "left", "right"]),
        button("Review your alerts", f"{origin}/#chart"),
        para("Extend any you still need. The rest will simply switch off."),
    ], Footer("You're receiving this because you have alerts in Pivot.", market=True))


def setup_copied(*, title: str, copies: int, url: str, origin: str = APP) -> Mail:
    return _mail("setup_copied", "product", f"Your shared setup “{title}” was copied",
                 "Another trader started from your setup.", [
        heading("Your setup was copied"),
        para(f"Another trader copied your shared setup “{title}” into their "
             "own workspace. They start from your drawings and indicators, and your "
             "name stays on it as the original author."),
        facts([("Setup", title), ("Copies so far", f"{copies:,}")]),
        button("View your setup", url),
        para("You can stop sharing at any time. Copies already made stay with "
             "the people who made them."),
    ], Footer("You're receiving this because you published a setup on Pivot.",
              Link("Unsubscribe", f"mailto:{CONTACT}?subject=Unsubscribe%20setup%20emails")))


def weekly_summary(*, name: str | None, week: str,
                   rows: Sequence[tuple[str, str, str]], alerts_fired: int,
                   origin: str = APP) -> Mail:
    """`rows` are (instrument, close, change over the week), already formatted
    from the bars. Nothing here is computed or estimated."""
    blocks = [
        heading("Your week in the markets"),
        para(_hi(name)),
        para(f"Here is how your watchlist closed the week of {week}."),
        table(["Instrument", "Close", "Week"], rows),
    ]
    blocks.append(para(
        "None of your alerts triggered this week." if alerts_fired == 0 else
        "1 of your alerts triggered this week." if alerts_fired == 1 else
        f"{alerts_fired} of your alerts triggered this week."))
    blocks.append(button("Open Pivot", origin))
    return _mail("weekly_summary", "product", f"Your watchlist, week of {week}",
                 f"How your watchlist closed the week of {week}.", blocks,
                 Footer("You're receiving this weekly summary because it is on for your account.",
                        Link("Unsubscribe", f"mailto:{CONTACT}?subject=Unsubscribe%20weekly%20summary"),
                        market=True))


def paper_fill(*, strategy: str, symbol: str, side: str, quantity: str,
               price: str, when: str, origin: str = APP) -> Mail:
    action = "bought" if side.lower().startswith("b") else "sold"
    return _mail("paper_fill", "product",
                 f"Paper trade: {action} {quantity} {symbol}",
                 f"{strategy} {action} {quantity} {symbol} at {price} in your paper book.", [
        heading("Your strategy placed a paper trade"),
        para(f"“{strategy}” {action} {quantity} {symbol} in your paper book."),
        facts([("Strategy", strategy), ("Instrument", symbol),
               ("Side", "Buy" if action == "bought" else "Sell"),
               ("Quantity", quantity), ("Fill price", price), ("Filled at", when)]),
        note("This is a simulated trade. No order went to a broker and no money moved."),
        button("Open the paper book", f"{origin}/paper"),
    ], Footer("You're receiving this because a strategy you armed placed a paper trade.",
              Link("Unsubscribe", f"mailto:{CONTACT}?subject=Unsubscribe%20paper%20trade%20emails"),
              market=True))


def order_confirm(*, order: str, play: str, where: str, url: str, minutes: int = 30,
                  reason: str = "", origin: str = APP) -> Mail:
    blocks = [
        heading("Confirm your order"),
        para(f"Your play “{play}” triggered. Its order waits for you."),
        facts([("Order", order), ("Placed in", where), ("Play", play)]),
    ]
    if reason:
        blocks.append(para(reason[0].upper() + reason[1:] + ("" if reason.endswith(".") else ".")))
    blocks += [
        button("Review and confirm", url),
        note(f"The button opens a page where you confirm or dismiss the order; it works for {minutes} "
             "minutes. You can also confirm it in Pivot. Pivot does not place orders on its own."),
    ]
    return _mail("order_confirm", "product", f"Confirm: {order}", f"{play} triggered. Confirm or dismiss it.",
                 blocks, Footer("You're receiving this because a play you armed proposed an order.", market=True))


# ── billing ─────────────────────────────────────────────────────────────────

def subscription_started(*, plan: str, cycle: str, amount: str, period: str,
                         renews: str, reference: str, invoice_url: str | None = None,
                         origin: str = APP) -> Mail:
    return _mail("subscription_started", "billing", f"Your Pivot {plan} plan is active",
                 f"Payment received: {amount}. Thank you.", [
        hero(f"Welcome to Pivot {plan}", "Your payment went through. Thank you."),
        para(f"Every {plan} feature is now on for your account."),
        facts([("Plan", f"{plan}, billed {cycle.lower()}"), ("Amount paid", amount),
               ("Current period", period), ("Renews on", renews),
               ("Payment reference", reference)]),
        button("View invoice" if invoice_url else "Manage billing",
               invoice_url or f"{origin}/settings/billing"),
        para("Your plan renews automatically. You can cancel anytime from your ",
             Link("billing settings", f"{origin}/settings/billing"),
             ", and you keep your plan until the end of the period you have paid for."),
    ], Footer(BILLING_REASON))


def payment_received(*, plan: str, cycle: str, amount: str, period: str,
                     renews: str, reference: str, invoice_url: str | None = None,
                     origin: str = APP) -> Mail:
    """A renewal went through. The first payment has `subscription_started`."""
    return _mail("payment_received", "billing", f"Payment received for Pivot {plan}",
                 f"Your {plan} plan renewed: {amount}. Thank you.", [
        heading("Your plan renewed"),
        para(f"We received {amount} for another period of Pivot {plan}. Nothing "
             "changes in your account."),
        facts([("Plan", f"{plan}, billed {cycle.lower()}"), ("Amount paid", amount),
               ("Period", period), ("Next renewal", renews),
               ("Payment reference", reference)]),
        button("View invoice" if invoice_url else "Manage billing",
               invoice_url or f"{origin}/settings/billing"),
    ], Footer(BILLING_REASON))


def payment_failed(*, plan: str, amount: str, retry_on: str | None = None,
                   access_until: str | None = None, origin: str = APP) -> Mail:
    blocks = [
        heading("Your payment didn't go through"),
        para(f"We tried to renew your {plan} plan for {amount}, but the payment was "
             "declined by your bank or card issuer. This often happens when a card "
             "expires or a mandate needs to be approved again."),
    ]
    if retry_on:
        blocks.append(para(f"We will try again on {retry_on}."))
    if access_until:
        blocks.append(para(f"Your {plan} features stay on until {access_until}."))
    blocks += [button("Update payment method", f"{origin}/settings/billing"),
               para("If the payment keeps failing, your account moves to the Free plan. "
                    "Your saved work stays in your account.")]
    return _mail("payment_failed", "billing", "Your Pivot payment didn't go through",
                 "Update your payment method to keep your plan.", blocks,
                 Footer(BILLING_REASON))


def renewal_reminder(*, plan: str, amount: str, date: str, cycle: str,
                     origin: str = APP) -> Mail:
    period = "year" if cycle.lower().startswith("a") else "month"
    return _mail("renewal_reminder", "billing", f"Your Pivot {plan} plan renews on {date}",
                 f"{amount} will be charged on {date}.", [
        heading(f"Your plan renews on {date}"),
        para(f"On {date}, {amount} will be charged to your saved payment method "
             f"for another {period} of Pivot {plan}."),
        para("There is nothing to do if you want to continue. To change plan or "
             "cancel, visit Billing before then."),
        button("Manage billing", f"{origin}/settings/billing"),
    ], Footer(BILLING_REASON))


def plan_changed(*, old_plan: str, new_plan: str, effective: str,
                 immediate: bool, charge: str | None = None,
                 origin: str = APP) -> Mail:
    if immediate:
        subject, head = f"You're now on Pivot {new_plan}", f"You're now on {new_plan}"
        lead = f"Your plan changed from {old_plan} to {new_plan} on {effective}."
    else:
        subject, head = f"Your plan changes to {new_plan} on {effective}", "Your plan change is scheduled"
        lead = (f"Your plan will change from {old_plan} to {new_plan} on {effective}, "
                f"at the end of your current period. Until then you keep {old_plan}.")
    blocks = [heading(head), para(lead)]
    if charge:
        blocks.append(facts([("Charged today", charge)]))
    blocks += [button("Manage billing", f"{origin}/settings/billing")]
    if not immediate:
        blocks.append(para("Changed your mind? You can undo this in Billing before "
                           f"{effective}."))
    return _mail("plan_changed", "billing", subject, lead, blocks, Footer(BILLING_REASON))


def subscription_cancelled(*, plan: str, access_until: str, origin: str = APP) -> Mail:
    return _mail("subscription_cancelled", "billing", "Your Pivot subscription is cancelled",
                 f"You keep {plan} until {access_until}.", [
        heading("Your subscription is cancelled"),
        para(f"Your {plan} plan will not renew. You keep every {plan} feature until "
             f"{access_until}, and then your account moves to the Free plan."),
        para("Your saved work, layouts and alerts stay in your account."),
        button("See plans", f"{origin}/pricing"),
        para("If something made you leave, we would like to hear it. Just reply to this email."),
    ], Footer(BILLING_REASON))


def refund_issued(*, amount: str, reference: str, original: str,
                  origin: str = APP) -> Mail:
    return _mail("refund_issued", "billing", "Your Pivot refund has been issued",
                 f"{amount} is on its way back to you.", [
        heading("Your refund has been issued"),
        para(f"We have refunded {amount} to the payment method you used."),
        facts([("Amount", amount), ("Refund reference", reference),
               ("Original payment", original)]),
        para("Banks usually take 5 to 7 working days to show a refund on your "
             "statement. If it hasn't arrived after that, reply with the refund "
             "reference and we will follow it up."),
    ], Footer("You're receiving this because a refund was issued on your Pivot account."))


# ── service ─────────────────────────────────────────────────────────────────

def policy_update(*, document: str, effective: str, changes: Sequence[str],
                  url: str, origin: str = APP) -> Mail:
    return _mail("policy_update", "service", f"An update to our {document}",
                 f"The changes take effect on {effective}.", [
        heading(f"We're updating our {document}"),
        para(f"The updated {document} takes effect on {effective}. In short:"),
        bullets(changes),
        button(f"Read the updated {document}", url),
        para("If you keep using Pivot after that date, the updated version applies. "
             "If you have a question or disagree with a change, reply to this email."),
    ], Footer("We send this to every Pivot account because it affects how the service works for you."))


def service_notice(*, title: str, when: str, affected: str,
                   body: Sequence[str], origin: str = APP) -> Mail:
    return _mail("service_notice", "service", title, f"{affected}: {when}.", [
        heading(title),
        facts([("When", when), ("What is affected", affected)]),
        *[para(p) for p in body],
        para("Your saved work, alerts and paper book are not affected unless "
             "this message says so."),
    ], Footer("We send service notices to accounts that the issue affects."))


def security_incident(*, summary: str, when: str, data: Sequence[str],
                      impact: str, actions_taken: Sequence[str],
                      your_steps: Sequence[str], origin: str = APP) -> Mail:
    """A personal-data breach notice. The sections follow what India's DPDP
    Rules ask a notice to cover: what happened and when, what data, the likely
    consequences, what we did, what the reader can do, and whom to contact."""
    return _mail("security_incident", "security",
                 "Important: a security incident involving your Pivot account",
                 "What happened, what it means for you, and what to do.", [
        heading("A security incident involved your account"),
        para("We are writing to tell you about a security incident that involved "
             "some of your information, what we have done about it, and what you can do."),
        subheading("What happened"),
        para(summary),
        facts([("When it happened", when)]),
        subheading("What information was involved"),
        bullets(data),
        subheading("What this could mean for you"),
        para(impact),
        subheading("What we have done"),
        bullets(actions_taken),
        subheading("What you can do"),
        bullets(your_steps),
        divider(),
        para("If you have any questions, reply to this email or write to ",
             Link(CONTACT, f"mailto:{CONTACT}"),
             ". A member of the team will answer personally."),
    ], Footer(SECURITY_REASON))


def support_received(*, reference: str, topic: str, origin: str = APP) -> Mail:
    return _mail("support_received", "account", f"We've received your message [{reference}]",
                 "A member of the team will reply within 2 working days.", [
        heading("We've received your message"),
        para(f"Thank you for writing to us about “{topic}”. "
             f"Your reference is {reference}."),
        para("A member of the team will reply within 2 working days. If this is a "
             "complaint, we will resolve it within one month and keep you updated "
             "along the way."),
        para("To add anything, reply to this email and keep the reference in the subject."),
    ], Footer("You're receiving this because you contacted Pivot."))


# ── samples, for the preview gallery and the tests ──────────────────────────
# Illustrative values only; nothing here is real market data.

def samples(origin: str = APP) -> dict[str, Mail]:
    reset = f"{origin}/reset-password?token=Qm9vdHN0cmFwLXNhbXBsZS10b2tlbg"
    return {
        "verify_email": verify_email(email="asha@example.com", name="Asha Rao",
                                     url=f"{origin}/verify-email?token=sample", origin=origin),
        "welcome": welcome(name="Asha Rao", origin=origin),
        "password_reset": password_reset(email="asha@example.com", url=reset, origin=origin),
        "password_changed": password_changed(email="asha@example.com",
                                             when="14 Oct 2026, 18:42 IST", origin=origin),
        "new_sign_in": new_sign_in(email="asha@example.com", when="14 Oct 2026, 18:42 IST",
                                   device="Chrome on macOS", location="Pune, India",
                                   origin=origin),
        "email_changed": email_changed(old_email="asha@example.com",
                                       new_email="asha.rao@example.in",
                                       when="14 Oct 2026, 18:42 IST", origin=origin),
        "account_closed": account_closed(email="asha@example.com", when="14 Oct 2026",
                                         origin=origin),
        "data_export_ready": data_export_ready(url=f"{origin}/account/export/sample",
                                               origin=origin),
        "waitlist_joined": waitlist_joined(email="asha@example.com", origin=origin),
        "access_granted": access_granted(email="asha@example.com", origin=origin),
        "alert_triggered": alert_triggered(
            symbol="RELIANCE", condition="Close crosses above 1,320", value="1,321.40",
            level="1,320", verb="crossed above", interval="15m",
            when="14 Oct 2026, 11:45 IST", alert_id=482,
            note_text="Breakout from the three-week range", origin=origin),
        "alerts_expiring": alerts_expiring(alerts=[
            ("TCS", "Close crosses below 3,050", "16 Oct 2026"),
            ("NIFTY 50", "RSI(14) rises above 70", "17 Oct 2026"),
            ("HDFCBANK", "Close crosses above 1,010", "19 Oct 2026")], origin=origin),
        "setup_copied": setup_copied(title="BANKNIFTY weekly range", copies=12,
                                     url=f"{origin}/#chart", origin=origin),
        "weekly_summary": weekly_summary(name="Asha Rao", week="6 to 10 Oct 2026", rows=[
            ("RELIANCE", "1,309.00", "+1.8%"), ("TCS", "3,082.50", "−0.6%"),
            ("HDFCBANK", "998.20", "+0.4%"), ("NIFTY 50", "25,108.30", "+1.1%")],
            alerts_fired=3, origin=origin),
        "paper_fill": paper_fill(strategy="20-day breakout", symbol="INFY", side="buy",
                                 quantity="25", price="1,512.40",
                                 when="14 Oct 2026, 09:16 IST", origin=origin),
        "subscription_started": subscription_started(
            plan="Pro", cycle="Monthly", amount="₹599.00 (GST included)",
            period="14 Oct to 13 Nov 2026", renews="14 Nov 2026",
            reference="pay_SAMPLE12345", origin=origin),
        "payment_received": payment_received(
            plan="Pro", cycle="Monthly", amount="₹599.00",
            period="16 Nov 2026 to 15 Dec 2026", renews="16 Dec 2026",
            reference="pay_Q8x2...", origin=origin),
        "payment_failed": payment_failed(plan="Pro", amount="₹599.00",
                                         retry_on="16 Nov 2026", access_until="21 Nov 2026",
                                         origin=origin),
        "renewal_reminder": renewal_reminder(plan="Pro", amount="₹5,988.00",
                                             date="14 Nov 2026", cycle="Annual",
                                             origin=origin),
        "plan_changed": plan_changed(old_plan="Pro", new_plan="Pro+", effective="14 Oct 2026",
                                     immediate=True, charge="₹500.00 (GST included)",
                                     origin=origin),
        "subscription_cancelled": subscription_cancelled(plan="Pro",
                                                         access_until="13 Nov 2026",
                                                         origin=origin),
        "refund_issued": refund_issued(amount="₹599.00", reference="rfnd_SAMPLE6789",
                                       original="pay_SAMPLE12345, 14 Oct 2026",
                                       origin=origin),
        "policy_update": policy_update(
            document="Privacy Policy", effective="14 Nov 2026",
            changes=["We now name every service provider that processes your data.",
                     "You can ask for a copy of your data from your account settings.",
                     "Our grievance officer's contact details are listed."],
            url=f"{SITE}/privacy", origin=origin),
        "service_notice": service_notice(
            title="Scheduled maintenance on 18 October", when="18 Oct 2026, 02:00 to 03:00 IST",
            affected="Charts, chat and alerts",
            body=["We are upgrading the servers that run Pivot. Charts and chat will be "
                  "unavailable for up to an hour.",
                  "Alerts that would have triggered during the window are checked as soon "
                  "as service resumes, and their emails say so."], origin=origin),
        "security_incident": security_incident(
            summary="On 12 October we found that an outdated server component allowed "
                    "unauthorised access to one of our servers. We closed the access the "
                    "same day.",
            when="Between 10 and 12 Oct 2026",
            data=["Your name and email address", "Your saved chart layouts"],
            impact="We have no evidence your information has been misused. The main risk "
                   "is emails that pretend to come from Pivot.",
            actions_taken=["Closed the access and replaced the affected server.",
                           "Signed out every session and rotated our credentials.",
                           "Reported the incident to CERT-In and the Data Protection Board."],
            your_steps=["Be careful with emails asking for your password. We never ask for it.",
                        "If you used your Pivot password anywhere else, change it there too."],
            origin=origin),
        "support_received": support_received(reference="PV-10482",
                                             topic="Invoice for October", origin=origin),
    }
