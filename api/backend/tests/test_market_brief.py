import asyncio
import json
import time
from types import SimpleNamespace
import pytest
from backend.cache import MockRedis
from backend.services import market_brief as mb

TOPICS = ['India', 'Global', 'Crypto', 'Commodities', 'Currencies & rates']
ROWS = [{'text': '**' + topic + ':** Verified updates are unavailable.', 'source_ids': []} for topic in TOPICS]
FACTS = {'india': {'advancers': 3}, 'news': [], 'quotes': []}

class SharedRedis:
    def __init__(self): self.cache = MockRedis()
    def get(self, key): return self.cache.get(key)
    def set(self, *args, **kwargs): return self.cache.set(*args, **kwargs)
    def eval(self, _script, _n, key, token):
        if self.get(key) == token.encode(): self.cache.delete(key)

@pytest.fixture()
def setup(monkeypatch):
    redis = SharedRedis()
    monkeypatch.setattr(mb, '_last', None)
    monkeypatch.setattr(mb, 'get_redis', lambda: redis)
    monkeypatch.setattr(mb.mx, 'build', lambda: {'as_of': {'prices': 'eod'}})
    monkeypatch.setattr(mb, 'context', lambda _: FACTS)
    calls = []
    async def complete(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(.01)
        return SimpleNamespace(content=json.dumps({'bullets': ROWS}), input_tokens=10, output_tokens=20)
    from backend.llm import factory
    monkeypatch.setattr(factory, 'get_llm_client', lambda: SimpleNamespace(complete=complete))
    return redis, calls

@pytest.mark.asyncio
async def test_shared_generation_is_not_keyed_by_ticks_or_user(setup):
    _, calls = setup
    result = await mb.refresh()
    assert result['source'] == 'model' and len(result['bullets']) == 5
    for _ in range(10):
        assert mb.cached()['generated_at'] == result['generated_at']
        await mb.refresh()
    assert len(calls) == 1

@pytest.mark.asyncio
async def test_concurrent_and_manual_refresh_share_one_call(setup):
    _, calls = setup
    await asyncio.gather(*[mb.refresh(force=True) for _ in range(8)])
    assert len(calls) == 1
    result = await mb.refresh(force=True)
    assert 'recently' in result['refresh_error'] and len(calls) == 1

@pytest.mark.asyncio
async def test_no_shared_redis_means_no_model_fanout(setup, monkeypatch):
    _, calls = setup
    monkeypatch.setattr(mb, 'get_redis', lambda: MockRedis())
    assert (await mb.refresh(force=True))['refresh_error']
    assert calls == []

@pytest.mark.asyncio
async def test_failure_keeps_previous_snapshot(setup, monkeypatch):
    redis, _ = setup
    previous = {'text': 'previous', 'bullets': ROWS, 'generated_at': time.time()-4000, 'source': 'model'}
    redis.set(mb.KEY, json.dumps(previous))
    def broken(_): raise RuntimeError('news failed')
    monkeypatch.setattr(mb, 'context', broken)
    result = await mb.refresh(force=True)
    assert result['text'] == 'previous' and result['stale'] and result['refresh_error']
    assert json.loads(redis.get(mb.KEY)) == previous

def test_bullet_grounding_sources_and_coverage():
    assert mb.parse_response(json.dumps({'bullets': ROWS}), FACTS) == ROWS
    for bad in [ROWS[:3], [{**row, 'source_ids': ['invented']} for row in ROWS],
                [{**row, 'text': row['text'] + ' Price 123456.'} for row in ROWS],
                [{**row, 'text': row['text'].replace('Crypto', 'Other')} for row in ROWS]]:
        with pytest.raises(ValueError): mb.parse_response(json.dumps({'bullets': bad}), FACTS)

def test_read_does_not_build_or_call_a_model(setup, monkeypatch):
    monkeypatch.setattr(mb.mx, 'build', lambda: pytest.fail('GET must not build'))
    assert mb.cached()['source'] == 'pending'
    assert setup[1] == []

@pytest.mark.asyncio
async def test_failed_automatic_attempt_is_not_repeated_each_minute(setup, monkeypatch):
    redis, calls = setup
    monkeypatch.setattr(mb, 'context', lambda _: (_ for _ in ()).throw(RuntimeError('offline')))
    await mb.refresh()
    redis.cache.delete(mb.COOLDOWN)  # five minutes passed, still inside the hour
    await mb.refresh()
    assert calls == []
    assert redis.get(mb.AUTO_ATTEMPT)

def test_rejects_overlong_and_duplicate_topic_bullets():
    long = [{**row, 'text': row['text'] + ' detail' * 40} for row in ROWS]
    duplicate = ROWS + [ROWS[0]]
    for rows in (long, duplicate):
        with pytest.raises(ValueError): mb.parse_response(json.dumps({'bullets': rows}), FACTS)

def test_grouped_headline_numbers_are_grounded_not_misread_as_separate_numbers():
    facts = {'headline': 'The index approached 8,000; turnover was 1,23,456.78.'}
    assert mb.mx.grounded('The report cites 8,000 and 123456.78.', facts)
    assert not mb.mx.grounded('The report cites 9,000.', facts)

def test_context_discards_old_future_and_unstamped_evidence(monkeypatch):
    now = time.time()
    monkeypatch.setattr(mb.mx, 'brief_facts', lambda _: {'advancers': 3})
    def read(path):
        if path.startswith('/quotes'):
            return {'quotes': [{'symbol': 'BTCUSDT', 'last': 1},
                {'symbol': 'USDINR', 'last': 2, 'as_of': now}]}
        return {'items': [
            {'title': 'old', 'link': 'https://example.test/old', 'ts': now-90000},
            {'title': 'future', 'link': 'https://example.test/future', 'ts': now+999},
            {'title': 'no date', 'link': 'https://example.test/missing'},
        ]}
    monkeypatch.setattr(mb, '_read', read)
    out = mb.context({'as_of': {}})
    assert out['news'] == []
    assert [q['symbol'] for q in out['quotes']] == ['USDINR']
