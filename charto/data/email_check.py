"""Is this an email address a person can actually receive mail at?

Sign-up used to accept anything with an "@" and a dot after it. This is the
check every address now passes before an account is made:

  1. Syntax, the way mail servers read it (email-validator, RFC 5321/5322):
     lengths, dots, quoting, internationalised domains, no IP literals, no
     special-use or reserved domains (example.com, .local, .test ...).
  2. The domain receives mail: it has MX records, or an address record that
     mail falls back to, and no "null MX" saying it accepts none. A DNS
     timeout does NOT refuse the address; a resolver hiccup must never stop a
     real person signing up. A domain that does not exist does.
  3. Not a throwaway inbox: the community blocklist of disposable domains
     (disposable_domains.txt, from github.com/disposable-email-domains, CC0;
     refresh with `curl -o` from that repo).
  4. One slip from Gmail, Yahoo, Outlook, Hotmail, iCloud or Rediffmail
     (gmial.com, yaho.com, outlok.com) is refused with the address that was
     meant: squatters run mail servers on those domains to collect what is
     misaddressed. Real providers that happen to look close (mail.com,
     ymail.com, gmx.com) are listed and never refused. Other near-misses that
     do not receive mail get the suggestion too; the confirmation email
     catches the rest.

The address is returned normalised: domain lowercased and IDNA-decoded, the
local part kept as typed but lowercased, since every account lookup here is
case-insensitive already.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

try:
    import email_validator as _ev
except ImportError:                 # the check degrades to syntax-only, loudly
    _ev = None

_BLOCKLIST = Path(__file__).with_name("disposable_domains.txt")

# The providers most Pivot accounts use, for near-miss suggestions.
_PROVIDERS = (
    "gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com",
    "live.com", "icloud.com", "rediffmail.com", "protonmail.com", "proton.me",
    "zoho.com", "aol.com", "msn.com",
)


@lru_cache(maxsize=1)
def _disposable() -> frozenset[str]:
    try:
        lines = _BLOCKLIST.read_text().splitlines()
    except OSError:
        return frozenset()
    return frozenset(l.strip().lower() for l in lines if l.strip() and not l.startswith("#"))


def is_disposable(domain: str) -> bool:
    """The domain or any parent of it is on the blocklist (a.b.mailinator.com)."""
    parts = domain.lower().split(".")
    block = _disposable()
    return any(".".join(parts[i:]) in block for i in range(len(parts) - 1))


def _distance(a: str, b: str) -> int:
    """Damerau-Levenshtein (optimal string alignment): a swap counts as one."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


# One slip from these is almost always a typo, and squatters run mail servers
# on the typo domains (yaho.com, outlok.com) to collect what is misaddressed.
_MAJOR = ("gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com",
          "icloud.com", "rediffmail.com")
# Real providers that sit one slip from a major one, never refused for it.
_REAL = frozenset({
    "mail.com", "gmx.com", "gmx.net", "ymail.com", "live.com", "live.in", "me.com",
    "mac.com", "aol.com", "msn.com", "zoho.com", "zohomail.in", "yandex.com",
    "inbox.com", "hey.com", "fastmail.com", "pm.me", "outlook.in", "hotmail.co.uk",
    "yahoo.in", "googlemail.com", "rediff.com",
})


def typo_of_major(domain: str) -> str | None:
    """The major provider this working-looking domain is one slip from."""
    domain = domain.lower()
    if domain in _MAJOR or domain in _REAL:
        return None
    for m in _MAJOR:
        if _distance(domain, m) == 1:
            return m
    return None


def suggestion(domain: str) -> str | None:
    """The big provider this domain is one slip away from, if any."""
    domain = domain.lower()
    if domain in _PROVIDERS:
        return None
    best = min(_PROVIDERS, key=lambda p: _distance(domain, p))
    return best if _distance(domain, best) <= (1 if len(domain) < 8 else 2) else None


def check(raw: str, *, deliverability: bool = True) -> tuple[str | None, str | None]:
    """(normalised address, None) when it is good; (None, reason) when not.

    The reason is a sentence for the person signing up."""
    address = str(raw or "").strip()
    if not address:
        return None, "Enter your email address."
    if len(address) > 254:
        return None, "That email address is too long."
    if _ev is None:
        local, _, domain = address.rpartition("@")
        if not local or "." not in domain or " " in address:
            return None, "Enter a valid email address."
        normalised = address.lower()
    else:
        try:
            info = _ev.validate_email(address, check_deliverability=False,
                                      allow_smtputf8=True)
        except _ev.EmailNotValidError:
            return None, "Enter a valid email address, like name@example.com."
        normalised = f"{info.local_part.lower()}@{info.domain.lower()}"
    domain = normalised.rsplit("@", 1)[1]
    near = suggestion(domain)
    typo = typo_of_major(domain)
    if typo:
        return None, f"Did you mean {normalised.rsplit('@', 1)[0]}@{typo}?"
    if is_disposable(domain):
        if near:                    # typo domains are on the blocklist too
            return None, f"Did you mean {normalised.rsplit('@', 1)[0]}@{near}?"
        return None, "Please use a permanent email address. Temporary inboxes can't receive your alerts or a password reset."
    if deliverability and _ev is not None:
        try:
            _ev.validate_email(normalised, check_deliverability=True, timeout=4)
        except _ev.EmailUndeliverableError:
            if near:
                return None, f"{domain} doesn't receive email. Did you mean {normalised.rsplit('@', 1)[0]}@{near}?"
            return None, f"{domain} doesn't receive email. Check the address and try again."
        except _ev.EmailNotValidError:
            return None, "Enter a valid email address, like name@example.com."
        except Exception:           # resolver trouble: never refuse a real person over it
            pass
    return normalised, None
