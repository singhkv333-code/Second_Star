# research/

The pipelines that load company research into Postgres (`pivot-db-india`).
Nothing here is a service: each script is run by hand, and the API and the
dataserver read what it wrote. Run them on the API's venv, which has the
database drivers and reads `api/.env` for the DSNs:

```
api/.venv/bin/python research/<script>.py --help
```

| Script | Writes | What it does |
|---|---|---|
| `build_identity.py` | `company_identity` | One verified row per company from the exchanges' own lists (Moneycontrol truncates names and mis-assigns tickers). |
| `extract_filings.py` | filings tables | Crawls quarterly result filings, with XBRL links, for every NSE-listed company. |
| `filing_pipeline.py` | filing facts | The full-universe annual-report run: discover, fetch, extract, model, in one process. `annual_report.py` in the API serves its output. |
| `filing_extract.py` | (library) | Segments annual reports, resolves their units, extracts the regular facts. |
| `filing_llm.py` | (library) | The model stage for what regex cannot read, behind three grounding gates. |
| `fetch_filings_sample.py` | (library) | The NSE/BSE session handling the crawlers share; also pulls a reconnaissance sample. |
| `load_mc_quarterly.py` | `quarterly_statement_lines` | Quarterly results for the whole universe from Moneycontrol's appfeeds host. |
| `build_quarterly_metrics.py` | `quarterly_metrics` | Derived quarterly ratios from the long statement lines (see `QUARTERLY_METRICS.md`). |
| `shp_pipeline.py`, `shp_parse.py` | shareholding tables | SEBI Reg-31 shareholding patterns, BSE first; the parser is a pure function with its own tests. |
| `pipeline_monitor.sh` | | Progress, rate and quality of a running universe job, read from Postgres. |

Model reads, code computes: every number a model extracts must be found in the
source text and pass the unit gates before it is stored. `filing_llm.py`'s
gates once caught a 10,000x unit error in the deterministic resolver.

```
api/.venv/bin/python -m pytest research/test_shp_parse.py -q
```
