"""httpx async client factory + retry helper."""
from __future__ import annotations

import random
from typing import Optional

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .config import Settings, get_settings


_RETRY_STATUSES = {429, 500, 502, 503, 504}


class TransientHTTPError(Exception):
    pass


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, TransientHTTPError):
        return True
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ConnectTimeout)):
        return True
    return False


def make_client(settings: Optional[Settings] = None) -> httpx.AsyncClient:
    settings = settings or get_settings()
    ua = random.choice(settings.user_agents)
    # Minimal headers that don't trip MC's Akamai bot-fingerprinting.
    # Browser-style UAs (Mozilla/Chrome) get 403'd after sustained crawling;
    # curl/python-requests UAs pass through. Skipping Accept-Language and
    # Referer also keeps the fingerprint closer to a CLI client.
    headers = {"User-Agent": ua, "Accept": "*/*"}
    transport = httpx.AsyncHTTPTransport(retries=0, http2=True)
    return httpx.AsyncClient(
        headers=headers,
        timeout=settings.http_timeout,
        follow_redirects=True,
        transport=transport,
        http2=True,
    )


async def fetch(
    client: httpx.AsyncClient, url: str, *, allow_404: bool = True
) -> Optional[httpx.Response]:
    """GET with backoff retry. Returns None on 404 (when allowed) or after retries exhausted."""
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    ):
        with attempt:
            resp = await client.get(url)
            if resp.status_code == 404:
                if allow_404:
                    return resp
                raise TransientHTTPError(f"404 not allowed for {url}")
            if resp.status_code in _RETRY_STATUSES:
                raise TransientHTTPError(f"status {resp.status_code} for {url}")
            return resp
    return None
