"""Isolated contracts: no DB connections, auth tables or service startup."""
import ast
import __future__
import io
import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException, Query, Header

ROOT = Path(__file__).resolve().parents[3]

def load_function(path, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec', flags=__future__.annotations.compiler_flag), namespace)
    return namespace[name]

class HistoryContracts(unittest.TestCase):
    def test_stock_patterns_are_cached_per_symbol_and_reject_a_market_response(self):
        cache = {}
        def cached(key, build):
            if key not in cache: cache[key] = build()
            return cache[key]
        ns = dict(Query=Query, Header=Header, HTTPException=HTTPException, json=json,
                  os=SimpleNamespace(environ={}), logger=SimpleNamespace(warning=lambda *a: None),
                  _auth=lambda auth: None, _resolve=lambda symbol: {'symbol':symbol}, _cached=cached)
        call=load_function('pivot/backend/routers/stock_detail.py','get_patterns',ns)
        def response(url, **kwargs):
            symbol=url.split('/patterns?')[0].split('/')[-1]
            body={'scope':'symbol','symbol':symbol,'patterns':[{'n':1 if symbol=='A' else 2}]}
            return io.BytesIO(json.dumps(body).encode())
        with patch('urllib.request.urlopen',response):
            self.assertEqual(call('A','1d',20,None)['patterns'][0]['n'],1)
            self.assertEqual(call('B','1d',20,None)['patterns'][0]['n'],2)
        self.assertEqual(len(cache),2)
        with patch('urllib.request.urlopen',lambda *a, **k: io.BytesIO(b'{"scope":"universe","symbol":"C"}')):
            with self.assertRaises(HTTPException) as exc: call('C','1d',20,None)
            self.assertEqual(exc.exception.status_code,502)
        self.assertEqual(len(cache),2)

    def test_all_statement_history_has_no_25_year_truncation(self):
        rows=[('Revenue',None,1,f'Mar {year}',date(year,3,31),float(year),str(year),'Rs. Cr.') for year in range(1980,2027)]
        session=SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchall=lambda:rows))
        ns=dict(resolve_symbol=lambda *a,**k:1,text=lambda sql:sql)
        call=load_function('pivot/backend/market/financials_db.py','get_statement',ns)
        self.assertEqual(len(call('A',years=0,session=session)['periods']),47)
        self.assertEqual(len(call('A',years=10,session=session)['periods']),10)

    def test_flat_prices_are_not_counted_as_bearish_hits_and_coverage_is_explicit(self):
        bars=[dict(t=i,o=100,h=101,l=99,c=100,v=10) for i in range(500)]
        patterns=SimpleNamespace(CANDLE_KINDS={'hanging_man'},CHART_KINDS=set(),
            candlesticks=lambda rows,*a,**k:[{'pattern':'hanging_man','bars_ago':499-i,'direction':'bearish'} for i in range(0,490,5)],
            chart_patterns=lambda *a,**k:[])
        limits=[]
        def get_bars(sym, interval, to, limit):
            limits.append(limit); return dict(bars=bars, earliest=0,latest=499,has_more=False)
        ns=dict(get_bars=get_bars,patterns=patterns,_OWN_BARS={'1d':100000},_OWN_FLOOR=20,
            _atr=lambda *a:[],_pivots=lambda *a:[],_tolerance=lambda *a:0,_EDGE_ONLY=set())
        call=load_function('charto/data/dataserver.py','_own_pattern_stats',ns)
        stats,coverage=call('A','1d',5)
        self.assertEqual(stats['hanging_man']['rate'],0)
        self.assertEqual(stats['hanging_man']['control'],0)
        self.assertEqual(coverage['bars_scanned'],500)
        self.assertFalse(coverage['has_more'])
        self.assertEqual(limits,[100000])

if __name__ == '__main__': unittest.main()
