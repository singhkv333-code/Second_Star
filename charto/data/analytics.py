"""Product analytics and error monitoring: PostHog, from the server.

Configured by the environment, never by source:

    POSTHOG_PROJECT_TOKEN   the project's ingestion token (phc_…); empty = off
    POSTHOG_HOST            ingestion host, default https://us.i.posthog.com

Both are in /etc/pivot/runtime.env on the VM, the file Pivot's API reads for
the same project (pivot/backend/posthog_client.py), so the two services report
into one place.

Rules, the same as Pivot's client:
- distinct_id is `charto:<user id>` — the prefix keeps charto's account ids
  apart from Pivot's, which number a different table. The browser identifies
  with the same id (preview/js/analytics.js), so a person's page views and
  their server events are one timeline.
- No PII in event properties: counts, flags, enum strings, tool names.
- Analytics never breaks a request. Every call is wrapped; a down PostHog
  costs a log line, not a turn.

Events are queued and sent from the SDK's own thread. They are flushed when
the process exits — including on SIGTERM, which systemd sends on restart and
which Python would otherwise answer by dying without running atexit.
"""
from __future__ import annotations

import atexit
import logging
import os
import signal

_client = None
_key = ""
_host = ""


def init() -> bool:
    """Start the client if the environment names a project. Idempotent."""
    global _client, _key, _host
    if _client is not None:
        return True
    _key = (os.environ.get("POSTHOG_PROJECT_TOKEN") or "").strip()
    _host = (os.environ.get("POSTHOG_HOST") or "https://us.i.posthog.com").strip().rstrip("/")
    if not _key:
        return False
    try:
        from posthog import Posthog
    except ImportError:
        logging.warning("charto analytics: posthog package not installed — off")
        return False
    try:
        _client = Posthog(project_api_key=_key, host=_host,
                          enable_exception_autocapture=True)
    except Exception as exc:  # noqa: BLE001
        logging.warning("charto analytics: init failed: %s", exc)
        return False
    atexit.register(_shutdown)
    _flush_on_sigterm()
    logging.info("charto analytics: PostHog on (host=%s)", _host)
    return True


def _shutdown() -> None:
    if _client is not None:
        try:
            _client.shutdown()
        except Exception:  # noqa: BLE001
            pass


def _flush_on_sigterm() -> None:
    """Flush, then die of SIGTERM exactly as before. Only when nothing else
    has claimed the signal, so an existing handler is never displaced."""
    try:
        if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
            return

        def _term(signum, _frame):
            _shutdown()
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        signal.signal(signal.SIGTERM, _term)
    except ValueError:
        pass                    # not the main thread (tests): atexit still flushes


def config() -> dict:
    """What the browser needs to report into the same project. The token is
    PostHog's public ingestion key — the one browser snippets embed — so
    serving it is by design; it can write events, never read them."""
    return {"key": _key, "host": _host} if _client is not None else {}


def distinct(uid) -> str:
    return f"charto:{uid}"


def capture(uid, event: str, props: dict | None = None) -> None:
    """One event. `uid` None = an anonymous server event (no person made)."""
    if _client is None:
        return
    try:
        p = {"surface": "charto", **(props or {})}
        if uid is None:
            p["$process_person_profile"] = False
        _client.capture(event, distinct_id=distinct(uid if uid is not None else "server"),
                        properties=p)
    except Exception as exc:  # noqa: BLE001
        logging.debug("charto analytics: capture %s failed: %s", event, exc)


def exception(exc: BaseException, uid=None, props: dict | None = None) -> None:
    """A handled failure worth monitoring (it never reached excepthook)."""
    if _client is None:
        return
    try:
        _client.capture_exception(
            exc, distinct_id=distinct(uid if uid is not None else "server"),
            properties={"surface": "charto", **(props or {})})
    except Exception as e:  # noqa: BLE001
        logging.debug("charto analytics: exception capture failed: %s", e)
