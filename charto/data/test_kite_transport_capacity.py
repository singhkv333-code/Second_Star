"""Transport-only fixtures: no live database, account, socket or model call."""
import ast
from pathlib import Path
import sqlite3
import threading
import time
import types
import unittest
from unittest.mock import patch
from typing import Any, Sequence


class CapacityTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name('kite_stream.py').read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'KiteStream')
        self.ticks = []
        self.resets = []
        self.ns = dict(Any=Any, Sequence=Sequence, threading=threading, time=time,
                       DEFAULT_MAX_STALE_SESSIONS=2, MAX_FILL_MIN=1440,
                       ds=types.SimpleNamespace(_LIVE={}),
                       log=types.SimpleNamespace(info=lambda *a: None, warning=lambda *a, **k: None,
                                                 exception=lambda *a: None, error=lambda *a: None),
                       reset_cursors=lambda syms: self.resets.append(list(syms)) or 0,
                       on_tick=lambda sym, tick: self.ticks.append(sym), _as_int=int)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'kite-stream-fixture', 'exec'), self.ns)
        self.cls = self.ns['KiteStream']

    def stream(self, count):
        stream = self.cls([f'S{i}' for i in range(count)], fill_first=False)
        stream.symbols = list(stream.requested)
        stream.plan = lambda: []
        stream.token = lambda: 'fixture'
        stream._resolve_tokens = lambda token: None
        stream._tok_to_sym = {i: f'S{i}' for i in range(count)}
        stream._tok_to_syms = {i: [f'S{i}'] for i in range(count)}
        stream._sym_to_tok = {f'S{i}': i for i in range(count)}
        def ticker(token):
            return types.SimpleNamespace(connect=lambda **kw: None, close=lambda **kw: None)
        stream._build_ticker = ticker
        stream._connect_tickers = lambda: None
        return stream

    def test_splits_6001_tokens_into_three_sockets(self):
        stream = self.stream(6001)
        stream.start()
        self.assertEqual([len(t._pivot_tokens) for t in stream._tickers], [3000, 3000, 1])

    def test_capacity_refusal_is_explicit(self):
        stream = self.stream(9001)
        stream.start()
        self.assertIn('S9000', stream.refused)
        self.assertEqual(len(stream.symbols), 9000)
        self.assertEqual(len(stream._tickers), 3)

    def test_shared_contract_and_continuous_token_both_receive_ticks(self):
        stream = self.stream(1)
        stream._tok_to_syms[0] = ['CONTRACT', 'CONTINUOUS']
        stream._on_ticks(None, [{'instrument_token': 0}])
        self.assertEqual(self.ticks, ['CONTRACT', 'CONTINUOUS'])

    def test_reconnect_does_not_reseed_other_shards(self):
        stream = self.stream(6001)
        stream.start()
        first = stream._tickers[0]
        stream._on_reconnect(first, 1)
        self.assertEqual(self.resets[-1], first._pivot_symbols)
        self.assertNotIn('S3000', self.resets[-1])

    def test_one_reactor_start_then_other_shards_on_reactor_thread(self):
        callbacks, connections, scheduled = [], [], []
        reactor = types.SimpleNamespace(running=False)
        reactor.callWhenRunning = callbacks.append
        def schedule(fn, **kwargs):
            scheduled.append(fn)
            fn(**kwargs)
        reactor.callFromThread = schedule
        def connect(index, **kwargs):
            connections.append(index)
            if not reactor.running:
                reactor.running = True
                for fn in callbacks:
                    fn()
        stream = self.cls([], fill_first=False)
        stream._tickers = [types.SimpleNamespace(connect=lambda i=i, **kw: connect(i, **kw))
                           for i in range(3)]
        modules = {'twisted': types.ModuleType('twisted'),
                   'twisted.internet': types.SimpleNamespace(reactor=reactor)}
        with patch.dict('sys.modules', modules):
            stream._connect_tickers()
        self.assertEqual(connections, [0, 1, 2])
        self.assertEqual(len(scheduled), 2)

    def test_existing_reactor_receives_all_shards_without_new_start(self):
        scheduled = []
        reactor = types.SimpleNamespace(running=True,
            callFromThread=lambda fn, **kw: scheduled.append(fn))
        stream = self.cls([], fill_first=False)
        stream._tickers = [types.SimpleNamespace(connect=lambda **kw: None) for _ in range(2)]
        modules = {'twisted': types.ModuleType('twisted'),
                   'twisted.internet': types.SimpleNamespace(reactor=reactor)}
        with patch.dict('sys.modules', modules):
            stream._connect_tickers()
        self.assertEqual(len(scheduled), 2)


if __name__ == '__main__':
    unittest.main()
