"""One public hourly brief, independent of user/page-load frequency.

Redis is required for distributed generation leases. If it is unavailable,
serve the previous snapshot / an honest pending response without calling an LLM.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
import uuid

from backend.cache import get_redis, MockRedis
from backend.services import market_explore as mx

logger = logging.getLogger(__name__)
KEY = 'markets:brief:shared:v3'
LEASE = KEY + ':lease'
COOLDOWN = KEY + ':refresh'
AUTO_ATTEMPT = KEY + ':hourly_attempt'
HOUR = 3600
_last: dict | None = None
PROMPT = Path(__file__).resolve().parents[1] / 'prompts/modules/market_brief.md'
SYMBOLS = ['NIFTY 50', 'NIFTY BANK', 'GOLD', 'SILVER', 'CRUDEOIL', 'USDINR', 'BTCUSDT', 'ETHUSDT']


def cached() -> dict:
    global _last
    try:
        raw = get_redis().get(KEY)
        if raw:
            value = json.loads(raw)
            if isinstance(value, dict) and value.get('bullets') and isinstance(value.get('generated_at'), (int, float)):
                _last = value
    except Exception:
        pass
    if not _last:
        return {'text': '', 'bullets': [], 'source': 'pending', 'sources': [],
                'generated_at': None, 'as_of': None, 'stale': True}
    return {**_last, 'stale': time.time() - _last['generated_at'] >= HOUR}


def _read(path: str) -> dict:
    origin = os.environ.get('CHARTO_UPSTREAM', 'http://127.0.0.1:5174').rstrip('/')
    with urllib.request.urlopen(origin + path, timeout=12) as response:
        return json.load(response)


def context(data: dict) -> dict:
    now = time.time()
    sources = []
    try:
        def feed(source):
            try:
                return _read('/feeds?sources=' + source + '&limit=6').get('items', [])
            except Exception:
                return []
        with ThreadPoolExecutor(max_workers=4) as pool:
            feeds = {'items': [item for batch in pool.map(feed, ('et-markets', 'cnbc-market', 'bbc-business', 'coindesk')) for item in batch]}
        # Allocate a fair share per publisher; busy Indian feeds must not
        # crowd global/crypto evidence out of the model's bounded context.
        counts: dict[str, int] = {}
        for item in feeds.get('items', []):
            ts = item.get('ts')
            publisher = str(item.get('source', ''))
            link = str(item.get('link', ''))
            if not isinstance(ts, (int, float)) or not 0 <= now - ts <= 86400:
                continue
            if not link.startswith(('https://', 'http://')) or not item.get('title') or counts.get(publisher, 0) >= 6:
                continue
            counts[publisher] = counts.get(publisher, 0) + 1
            sources.append({'id': f'N{len(sources)+1}', 'title': str(item['title'])[:220],
                'summary': str(item.get('summary', ''))[:240], 'url': link,
                'source': publisher, 'published_at': ts})
    except Exception:
        logger.info('market brief: news unavailable')
    quotes = []
    try:
        payload = _read('/quotes?' + urllib.parse.urlencode({'symbols': ','.join(SYMBOLS)}))
        for q in payload.get('quotes', []):
            if q.get('symbol') not in SYMBOLS or mx._num(q.get('last')) is None:
                continue
            # A price without an observation time is not current evidence.
            ts = mx._num(q.get('as_of'))
            if ts is None or not 0 <= now - ts <= 4 * 86400:
                continue
            quotes.append({k: q.get(k) for k in ('symbol', 'last', 'change_pct', 'currency', 'as_of', 'source')})
    except Exception:
        logger.info('market brief: cross-asset quotes unavailable')
    for i, q in enumerate(quotes):
        q['id'] = f'Q{i+1}'
    return {'as_of': data['as_of'], 'india': {'id': 'I1', **mx.brief_facts(data)}, 'news': sources, 'quotes': quotes}


def parse_response(text: str, facts: dict) -> list[dict]:
    result = json.loads(text)
    rows = result.get('bullets', [])
    allowed = {x['id'] for x in facts['news'] + facts['quotes']} | {'I1'}
    if not isinstance(rows, list) or not 5 <= len(rows) <= 6:
        raise ValueError('Expected 5–6 bullets')
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('text'), str) or not 10 <= len(row['text']) <= 320:
            raise ValueError('Invalid bullet')
        if len(row['text'].split()) > 36:
            raise ValueError('Bullet too long')
        ids = row.get('source_ids', [])
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in allowed for i in ids):
            raise ValueError('Unknown source')
        if not mx.grounded(row['text'], facts) or '[' in row['text'] or '<' in row['text']:
            raise ValueError('Ungrounded text or embedded link')
        if not row['text'].startswith('**India:') and not ids and 'unavailable' not in row['text'].lower():
            raise ValueError('Cross-market claim needs evidence')
    if not all(any(row['text'].startswith('**' + topic + ':') for row in rows)
               for topic in ('India', 'Global', 'Crypto', 'Commodities', 'Currencies & rates')):
        raise ValueError('Missing market category')
    labels = [row['text'].split('**', 2)[1] for row in rows if row['text'].startswith('**')]
    if len(labels) != len(set(labels)) or sum(len(row['text'].split()) for row in rows) > 180:
        raise ValueError('Repeated topic or overlong brief')
    return rows


async def refresh(*, force: bool = False) -> dict:
    global _last
    current = cached()
    if not force and not current['stale']:
        return current
    redis = get_redis()
    if isinstance(redis, MockRedis):
        return {**current, 'refresh_error': 'Shared brief storage is unavailable.'}
    token = uuid.uuid4().hex
    try:
        if not redis.set(LEASE, token, nx=True, ex=180):
            return {**current, 'refreshing': True}
    except Exception:
        return {**current, 'refresh_error': 'Shared brief storage is unavailable.'}
    try:
        if not force and not redis.set(AUTO_ATTEMPT, token, nx=True, ex=HOUR):
            return current
        if not redis.set(COOLDOWN, token, nx=True, ex=300):
            return {**current, 'refresh_error': 'The shared brief was refreshed recently. Please try later.'}
        data = await asyncio.wait_for(asyncio.to_thread(mx.build), timeout=45)
        facts = await asyncio.wait_for(asyncio.to_thread(context, data), timeout=30)
        from backend.llm.base import LLMMessage
        from backend.llm.factory import get_llm_client
        started = time.monotonic()
        response = await asyncio.wait_for(get_llm_client().complete(
            messages=[LLMMessage(role='system', content=PROMPT.read_text()),
                      LLMMessage(role='user', content=json.dumps(facts))],
            tools=None, tool_choice='none', max_output_tokens=2200,
            reasoning_effort='low', temperature=0.2), timeout=55)
        rows = parse_response(response.content or '', facts)
        out = {'bullets': rows, 'text': '\n'.join('- ' + x['text'] for x in rows),
               'source': 'model', 'generated_at': time.time(), 'as_of': data['as_of'],
               'sources': facts['news'], 'quotes': facts['quotes'], 'stale': False}
        # Publish only complete, validated snapshots; failures never replace
        # yesterday's useful brief with a partial result.
        redis.set(KEY, json.dumps(out), ex=48 * HOUR)
        _last = out
        logger.info('market brief generated: latency=%.2fs input_tokens=%s output_tokens=%s verdict=grounded/%d-bullets',
                    time.monotonic() - started, getattr(response, 'input_tokens', None),
                    getattr(response, 'output_tokens', None), len(rows))
        return out
    except Exception as exc:
        logger.warning('market brief refresh failed: %s', type(exc).__name__)
        return {**current, 'refresh_error': 'Refresh unavailable; the previous brief is unchanged.'}
    finally:
        # Compare-and-delete: an expired worker cannot remove a newer lease.
        try:
            redis.eval("if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end", 1, LEASE, token)
        except Exception:
            pass  # lease expires; do not delete another worker's lock


async def hourly_loop() -> None:
    while True:
        try:
            await refresh()
        except Exception as exc:
            logger.warning('market brief worker unavailable: %s', type(exc).__name__)
        # Check freshness cheaply; manual refresh shifts the next due time.
        # AUTO_ATTEMPT bounds failures to one automatic model attempt/hour.
        await asyncio.sleep(60)
