"""One-time Telegram (Telethon) authentication helper.

Run once on the production host (or wherever the news_events
Telegram worker lives) to create the ``.session`` file that the
long-running worker reuses on every boot.

Usage:

    export TELEGRAM_API_ID=1234567
    export TELEGRAM_API_HASH=abc...
    export TELEGRAM_SESSION_PATH=/var/lib/pivot/telegram.session
    python -m scripts.auth_telegram

The script will prompt for your phone number and the SMS code
Telegram sends. After a successful run, the .session file is on
disk and the worker can start without any further prompts.

Re-run this script if Telegram invalidates the session (rare:
24h flood ban, password change, etc.).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_dotenv_if_present() -> None:
    """Load ``pivot/.env`` into ``os.environ`` so the script reads the
    same credentials as the running backend. python-dotenv ships in
    requirements.txt; if it's somehow missing we fall back to a
    minimal hand-parse so the script still works."""
    project_root = Path(__file__).resolve().parents[1]
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    # Minimal fallback parser — KEY=value, ignore # comments.
    with env_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def main() -> int:
    _load_dotenv_if_present()

    try:
        from telethon import TelegramClient  # type: ignore[import-untyped]
    except ImportError:
        print(
            "ERROR: telethon is not installed. Run: pip install telethon",
            file=sys.stderr,
        )
        return 1

    api_id_raw = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session_path = os.environ.get(
        "TELEGRAM_SESSION_PATH", "/var/lib/pivot/telegram.session"
    ).strip()

    if not api_id_raw or not api_hash:
        print(
            "ERROR: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set.\n"
            "Get them from https://my.telegram.org → API development tools.",
            file=sys.stderr,
        )
        return 1
    try:
        api_id = int(api_id_raw)
    except ValueError:
        print(
            f"ERROR: TELEGRAM_API_ID must be an integer (got {api_id_raw!r}).",
            file=sys.stderr,
        )
        return 1

    parent_dir = os.path.dirname(session_path)
    if parent_dir and not os.path.isdir(parent_dir):
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except OSError as exc:
            print(
                f"ERROR: cannot create session dir {parent_dir}: {exc}",
                file=sys.stderr,
            )
            return 1

    print(f"Opening Telegram session at: {session_path}")
    print("You will be prompted for your phone number and a one-time code.")
    print("This only needs to happen once per session file.\n")

    client = TelegramClient(session_path, api_id, api_hash)
    # ``with client:`` runs Telethon's sync wrapper which calls
    # ``client.start()`` (interactive phone + SMS prompts). Inside the
    # block we still need to drive coroutines through the same
    # synchronous loop helper.
    with client:
        try:
            me = client.loop.run_until_complete(client.get_me())
            username = getattr(me, "username", None) or getattr(me, "first_name", "")
            print(
                f"\n✓ Authenticated as @{username} (user_id={me.id})."
            )
        except Exception as exc:  # noqa: BLE001 — cosmetic only
            print(f"\n✓ Authenticated. (get_me() cosmetic step skipped: {exc})")
        print(f"✓ Session file written to {session_path}")
        print(
            "\nNext: set TELEGRAM_ENABLED=true in .env, restart the backend, "
            "and the Telegram worker will start."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
