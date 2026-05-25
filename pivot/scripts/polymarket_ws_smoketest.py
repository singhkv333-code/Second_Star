"""Polymarket CLOB market-data WS smoke test.

One-shot: connects to wss://ws-subscriptions-clob.polymarket.com/ws/market,
subscribes to a single YES token, prints every inbound message for ~60s,
and exits cleanly. Used to verify the wire format and our handshake
assumptions BEFORE wiring the production client.

Usage:
    python pivot/scripts/polymarket_ws_smoketest.py            # auto-pick top-volume open market
    python pivot/scripts/polymarket_ws_smoketest.py <token_id> # explicit token
    python pivot/scripts/polymarket_ws_smoketest.py --duration 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
from typing import Optional

import certifi
import httpx
import websockets


def _ssl_ctx() -> ssl.SSLContext:
    """macOS Python ships without the system CA bundle, so the default
    SSL context fails on wss://. Pin certifi's bundle explicitly."""
    return ssl.create_default_context(cafile=certifi.where())


GAMMA_URL = "https://gamma-api.polymarket.com/markets"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _pick_top_volume_yes_token() -> tuple[str, str, str]:
    """Return (token_id, question, yes_price_str) for the highest-volume
    open binary market. Used when no token id is passed on the CLI."""
    params = {
        "closed": "false",
        "active": "true",
        "limit": 25,
        "order": "volume24hr",
        "ascending": "false",
    }
    r = httpx.get(GAMMA_URL, params=params, timeout=15.0)
    r.raise_for_status()
    for market in r.json():
        outcomes = market.get("outcomes")
        prices = market.get("outcomePrices")
        token_ids = market.get("clobTokenIds")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(prices, str):
            prices = json.loads(prices)
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)
        if not (isinstance(outcomes, list) and isinstance(token_ids, list)
                and len(outcomes) == len(token_ids)):
            continue
        for label, tok, price in zip(outcomes, token_ids, prices or []):
            if str(label).strip().lower() in {"yes", "true"}:
                return str(tok), str(market.get("question", "")), str(price)
    raise RuntimeError("no binary YES/NO market with token ids found")


async def _run(token_id: str, duration_s: float) -> int:
    """Open WS, subscribe to one asset, print messages until timeout.

    Returns:
        0 on a clean run with ≥1 message received,
        2 if connection succeeded but no messages arrived,
        3 on connection / protocol failure.
    """
    # `custom_feature_enabled: true` opts the connection into the
    # heavier event types: best_bid_ask, new_market, market_resolved.
    # No auth required — it's an opt-in flag, not a credential.
    sub_payload = {
        "type": "Market",
        "assets_ids": [token_id],
        "custom_feature_enabled": True,
    }
    print(f"[smoke] connect → {WS_URL}")
    print(f"[smoke] subscribe payload → {json.dumps(sub_payload)}")
    print(f"[smoke] listening for {duration_s:.0f}s …", flush=True)

    msg_count = 0
    msg_kinds: dict[str, int] = {}
    started = time.monotonic()
    try:
        async with websockets.connect(WS_URL, max_size=2**22, ssl=_ssl_ctx()) as ws:
            await ws.send(json.dumps(sub_payload))
            while True:
                remaining = duration_s - (time.monotonic() - started)
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                msg_count += 1
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"[smoke] non-JSON frame: {raw!r}")
                    continue
                # Polymarket sometimes batches multiple events in one
                # frame as a JSON array — handle both shapes.
                events = payload if isinstance(payload, list) else [payload]
                for ev in events:
                    kind = str(ev.get("event_type", ev.get("type", "?")))
                    msg_kinds[kind] = msg_kinds.get(kind, 0) + 1
                    print(f"[smoke] {kind} → {json.dumps(ev)[:240]}",
                          flush=True)
    except (websockets.ConnectionClosed, OSError) as exc:
        print(f"[smoke] connection error: {exc}", file=sys.stderr)
        return 3

    print(f"[smoke] done. frames={msg_count} kinds={msg_kinds}")
    return 0 if msg_count > 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("token_id", nargs="?", default=None,
                    help="CLOB token id; auto-pick top-volume YES if omitted")
    ap.add_argument("--duration", type=float, default=45.0,
                    help="seconds to listen before exiting (default 45)")
    args = ap.parse_args()

    if args.token_id:
        token, question, yes_price = args.token_id, "<cli-provided>", "?"
    else:
        token, question, yes_price = _pick_top_volume_yes_token()
        print(f"[smoke] picked YES token for: {question!r}")
        print(f"[smoke] current YES price: {yes_price}")
        print(f"[smoke] token_id: {token}")

    return asyncio.run(_run(token, args.duration))


if __name__ == "__main__":
    sys.exit(main())
