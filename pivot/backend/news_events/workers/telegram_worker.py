"""Long-lived Telethon client for the Tier-A Telegram push transport.

Unlike the RSS poller and the funnel drain, this worker is NOT a
fixed-interval APScheduler job — it opens one Telethon client at
app startup and keeps the connection open. New messages on the
configured channels arrive via Telethon's ``events.NewMessage``
callback and are persisted into ``news_articles`` synchronously via
``persist_pushed_items``.

Setup:
  1. Get ``TELEGRAM_API_ID`` + ``TELEGRAM_API_HASH`` from
     https://my.telegram.org → API development tools.
  2. Run ``scripts/auth_telegram.py`` once on the production host to
     create the ``.session`` file (interactive SMS code).
  3. Set ``TELEGRAM_ENABLED=true``. The worker will start
     automatically on the next app boot.

If Telethon isn't installed (``pip install telethon`` was skipped)
or the credentials are missing, the worker logs a warning and
exits cleanly — the rest of the news_events subsystem still works.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from backend.config import settings
from backend.database import SessionLocal
from backend.news_events.pipeline.ingest import persist_pushed_items
from backend.news_events.sources.telegram_source import (
    configured_telegram_channels,
    translate_event,
)

logger = logging.getLogger(__name__)


# Module-level handle so the FastAPI shutdown hook can stop us
# gracefully without yet another import cycle.
_running_task: Optional[asyncio.Task] = None


def _telethon_available() -> bool:
    try:
        import telethon  # noqa: F401
    except ImportError:
        return False
    return True


def _config_complete() -> bool:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        logger.warning(
            "[news_events.telegram] credentials missing — set "
            "TELEGRAM_API_ID + TELEGRAM_API_HASH; worker disabled"
        )
        return False
    if not os.path.exists(settings.telegram_session_path):
        logger.warning(
            "[news_events.telegram] session file not found at %r — "
            "run scripts/auth_telegram.py once to create it; worker disabled",
            settings.telegram_session_path,
        )
        return False
    return True


async def _handle_new_message(event) -> None:
    """Called on every new message in any subscribed channel."""
    try:
        item = translate_event(event)
    except Exception:  # noqa: BLE001 — never let a translator bug kill the worker
        logger.exception("[news_events.telegram] translator failure")
        return
    if item is None:
        return

    # Synchronous DB work; offload to a worker thread so the
    # Telethon event loop isn't blocked on DB flushes.
    def _persist() -> tuple[int, int]:
        db = SessionLocal()
        try:
            outcome = persist_pushed_items(
                db, source_id=item.source_id, items=[item]
            )
            db.commit()
            return outcome.new_count, outcome.after_stage2
        finally:
            db.close()

    try:
        new_count, after_stage2 = await asyncio.to_thread(_persist)
    except Exception:  # noqa: BLE001
        logger.exception(
            "[news_events.telegram] persist failure for source=%s",
            item.source_id,
        )
        return

    logger.info(
        "[news_events.telegram] received source=%s msg_id=%s new=%d "
        "stage2_passed=%d",
        item.source_id,
        (item.raw_metadata or {}).get("telegram_message_id"),
        new_count,
        after_stage2,
    )


async def _run_client() -> None:
    """Open the Telethon client, register the handler, run forever
    until cancelled."""
    if not _telethon_available():
        logger.warning(
            "[news_events.telegram] telethon not installed — "
            "pip install telethon to enable"
        )
        return
    if not _config_complete():
        return

    channels = configured_telegram_channels()
    if not channels:
        logger.warning(
            "[news_events.telegram] no channels configured — "
            "registry has no kind='telegram' entries"
        )
        return

    # Lazy import so the rest of the subsystem doesn't depend on
    # telethon being present.
    from telethon import TelegramClient, events  # type: ignore[import-untyped]

    client = TelegramClient(
        settings.telegram_session_path,
        int(settings.telegram_api_id),
        settings.telegram_api_hash,
    )

    chat_usernames = [u for _sid, u in channels]
    client.add_event_handler(
        _handle_new_message,
        events.NewMessage(chats=chat_usernames),
    )

    # Fail-fast callbacks: telethon's interactive prompts would otherwise
    # hit stdin (which is closed under uvicorn) and trip EOFError after
    # confusing the session. We supply callbacks that raise a clear
    # exception, so a dead/missing session surfaces as one warning log
    # line instead of an EOF traceback.
    async def _no_interactive_prompt(*_args, **_kwargs):  # noqa: ANN001
        raise RuntimeError(
            "session is not authenticated — re-run scripts/auth_telegram.py"
        )

    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning(
                "[news_events.telegram] session is NOT authenticated — "
                "re-run scripts/auth_telegram.py from a real terminal "
                "and restart the backend; worker exiting cleanly"
            )
            await client.disconnect()
            return
        # is_user_authorized was True; finish the start sequence with
        # callbacks that never prompt.
        await client.start(
            phone=_no_interactive_prompt,
            code_callback=_no_interactive_prompt,
            password=_no_interactive_prompt,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[news_events.telegram] client.start() failed: %s — "
            "session may be invalid; re-run scripts/auth_telegram.py",
            exc,
        )
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return

    logger.info(
        "[news_events.telegram] client connected, listening on %d channels: %s",
        len(chat_usernames),
        ", ".join(chat_usernames),
    )

    try:
        await client.run_until_disconnected()
    except asyncio.CancelledError:
        logger.info("[news_events.telegram] worker cancelled, shutting down")
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


def start_telegram_worker(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Boot the Telegram client as a fire-and-forget asyncio task.

    Idempotent — re-calling while a worker is already running is a
    no-op. Called from main.py's startup hook behind the
    ``telegram_enabled`` flag.
    """
    global _running_task
    if _running_task is not None and not _running_task.done():
        logger.info("[news_events.telegram] worker already running, skipping")
        return

    target_loop = loop or asyncio.get_event_loop()
    _running_task = target_loop.create_task(
        _run_client(), name="news_events.telegram_worker"
    )
    logger.info("[news_events.telegram] worker task scheduled")


async def stop_telegram_worker() -> None:
    """Graceful shutdown hook. Cancels the running task and waits
    for ``run_until_disconnected`` to return cleanly."""
    global _running_task
    if _running_task is None or _running_task.done():
        return
    _running_task.cancel()
    try:
        await _running_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _running_task = None
