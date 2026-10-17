"""Bound a blocking network call so it can never hang a request.

Some yfinance calls — notably ``Ticker.info``, ``Ticker.news`` and
``Ticker.get_earnings_dates`` — accept no ``timeout`` argument and, on a cloud
(datacenter) IP where Yahoo silently drops the connection, block for a very
long time. In a FastAPI sync handler (run in a threadpool) that manifests as the
browser's "Failed to fetch" when the gateway timeout trips.

``call_bounded`` runs the callable in a daemon worker thread and returns its
result, or ``default`` if it exceeds ``timeout`` seconds. The worker thread is
abandoned on timeout (it unwinds on its own once the socket finally errors), but
the request returns immediately — a fast, clean fallback instead of a hang.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FTimeout
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# A small shared pool of daemon threads. Bounded so a burst of hung yfinance
# calls can't spawn unbounded OS threads; excess calls queue and are themselves
# subject to the caller's timeout via future.result(timeout=...).
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="net-bounded")


def call_bounded(
    fn: Callable[..., T],
    *args: Any,
    timeout: float = 6.0,
    default: Optional[T] = None,
    label: str = "",
    **kwargs: Any,
) -> Optional[T]:
    """Run ``fn(*args, **kwargs)`` with a hard wall-clock ``timeout``.

    Returns the result, or ``default`` on timeout OR any exception (both are
    "the source didn't answer in time" from the caller's point of view).
    """
    fut = _POOL.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=timeout)
    except _FTimeout:
        logger.info("bounded call timed out after %ss%s", timeout,
                    f" ({label})" if label else "")
        return default
    except Exception as exc:  # noqa: BLE001 — surface as the clean fallback
        logger.info("bounded call failed%s: %s",
                    f" ({label})" if label else "", str(exc)[:160])
        return default
