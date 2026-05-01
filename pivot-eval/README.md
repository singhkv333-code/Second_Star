# pivot-eval

Read-only eval runner for the Pivot chatbot. Runs the 200-case dataset
in `Second_Star/Readme.md`, scores each response (deterministic checks +
Sarvam-as-judge with an anchored 1–3 scale), and writes:

- `runs/<ts>/conversations.md` — every transcript, fails first
- `runs/<ts>/results.json`     — machine-readable, per-criterion scores
- `runs/<ts>/report.md`        — two-page summary
- `runs/<ts>/suggestions.md`   — pattern-detection brief for the maintainer

**The eval never modifies the chatbot or its prompts.** Suggestions are a
brief; you decide what (if anything) to action.

## Setup

```bash
cd pivot-eval
cp .env.example .env       # tweak SARVAM_API_KEY + login creds
uv sync
uv run pytest tests/ -q    # 6 parser tests pass without a live backend
```

The runner expects the Pivot FastAPI backend at `PIVOT_BASE_URL`
(default `http://127.0.0.1:8000`). Start it with:

```bash
cd ../pivot && source .venv/bin/activate && uvicorn backend.main:app --port 8000
```

## Commands

```bash
# 1. Run a smoke subset
uv run pivot-eval run --filter CASUAL-01,FIN-01,MULTI-01 --sequential

# 2. Run a category
uv run pivot-eval run --filter CASUAL

# 3. Run everything (200 cases)
uv run pivot-eval run

# 4. List which cases match a filter (no execution)
uv run pivot-eval list-cases --filter MULTI

# 5. Regenerate report.md / conversations.md from an existing results.json
uv run pivot-eval report

# 6. Pattern-detection over the latest run → suggestions.md
uv run pivot-eval suggest
```

## How scoring works

Two stages, in this order:

1. **Deterministic checks first** — they're free, fast, and consistent:
   - `must_use_tool: X` → look up X in a tool-family map; check `tools_called`.
   - `ideal_length_words: 5-25` → word count.
   - `must_not: hallucinate_value` → auto-passes if any tool fired.
   - `must_not: unsolicited_investment_advice` → regex sweep.
   - `must_not: restate_full_capabilities` → presence of the canonical 4-liner.

2. **Sarvam judge for the rest** with the anchored prompt
   (`1=fails, 2=partial, 3=meets`). Judge defaults every score to 2 if the
   API key isn't set or the call errors — so the suite still produces useful
   output offline, just with wider grey zones.

A case **passes** if every `must_not` and `must_use_tool` scores 3 and the
mean of `should` items ≥ 2. **Partial** if shoulds drag below. **Fail** for
any hard violation.

## v1 quirks worth knowing

- **Tool-family mapping is deliberately loose.** A rubric that says
  `must_use_tool: get_quote` is satisfied by `get_live_price`, `get_ohlc`,
  or `run_compare` — they all return real price data. Edit
  `judge.py::_TOOL_FAMILY` to tighten if you want strict 1:1 matching.
- **Sarvam judging adds ~2-3s per case.** With concurrency=3 a full
  200-case run completes in ~5-7min.
- **Latency includes the chat round-trip and the judge call** — the report's
  P90/P99 reflect the worst observed Sarvam latency, not a chatbot
  regression on its own.
