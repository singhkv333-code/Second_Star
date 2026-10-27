"""Structlog configuration for the Pivot backend.

This module is the single entry point for log configuration. It does
three things:

1. Builds a structlog processor chain that merges per-request
   contextvars (request_id, user_id, route, …) into every log line.
2. Bridges the stdlib `logging` module through the same chain so that
   existing `logging.getLogger(__name__).info(...)` call sites keep
   working unchanged.
3. Silences a handful of chatty third-party loggers
   (sqlalchemy.engine, uvicorn.access, apscheduler) that would
   otherwise drown the signal.

`configure_logging()` is idempotent — safe to call from `main.py`,
from a worker entry point, or from tests.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import Processor

from backend.config import settings

_CONFIGURED: bool = False


def _shared_processors() -> list[Processor]:
    """Processors shared between structlog-native loggers and the
    stdlib bridge. Order matters: contextvars MUST come first so the
    request_id binding is visible to every later processor.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def configure_logging() -> None:
    """Wire up structlog + stdlib logging. Idempotent.

    Reads `settings.log_level` and `settings.log_format`. Honors
    `LOG_LEVEL` / `LOG_FORMAT` env vars by way of pydantic-settings.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level_name: str = (settings.log_level or "INFO").upper()
    log_level: int = getattr(logging, log_level_name, logging.INFO)
    log_format: str = (settings.log_format or "console").lower()

    renderer: Processor
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    shared = _shared_processors()

    # structlog-native loggers (structlog.get_logger(...)) use this
    # chain directly and render at the end.
    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Stdlib bridge: any `logging.getLogger(__name__).info(...)` call
    # in the codebase flows through ProcessorFormatter and uses the
    # same shared processor chain plus the same final renderer.
    formatter = structlog.stdlib.ProcessorFormatter(
        # Records produced from the stdlib side need a couple of
        # adapter processors before they hit the shared chain.
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.ExtraAdder(),
            *shared,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Reset any prior handlers so re-runs in tests / re-imports don't
    # multiply log lines.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(log_level)

    # Silence the firehose. These are well-known noisy loggers; the
    # signal they produce at INFO is rarely worth the cost.
    for noisy, lvl in (
        ("sqlalchemy.engine", logging.WARNING),
        ("uvicorn.access", logging.WARNING),
        ("apscheduler.executors.default", logging.WARNING),
    ):
        logging.getLogger(noisy).setLevel(lvl)

    _CONFIGURED = True
