"""The agent that turns a sentence into a validated custom indicator.

A generator, so the chat stream can show each stage as it happens without a
second thread (the server's request state is thread-local):

    understanding   ONE call: what is being asked, whether Charto already has
    + researching   it natively, which independent implementation exists, and
                    — when the model is not certain of the definition — a
                    hosted web search in the same call, whose citations are
                    taken from the search results, never from its memory
    coding          spec + compute() in the sandbox dialect
    testing         indicator_sandbox + custom_indicators.validate()
    repairing       the failing checks go back to the model verbatim — the
                    generate → execute → repair loop (AlphaCodium, LLMLOOP)
    rendering       stored, then handed to the chart through the same scene
                    item a native indicator uses

Every model call is schema-bound JSON (Responses API `text.format`), so a
reply is parsed, never scraped. The model writes the formula and the code;
code computes every number the user will see, and the validator — not the
model — decides whether it reaches a chart.
"""
from __future__ import annotations

import json
import time

import custom_indicators as ci
import indicators as native

MAX_ATTEMPTS = 3          # one draft + two repairs

# ── vocabulary the model is given ─────────────────────────────────────────
TA_DOC = """\
`ta` (Pine-like, every function causal, lists in → list out, None for warm-up):
  ta.sma(src, length)  ta.ema(src, length)  ta.rma(src, length)  [Wilder, alpha=1/length]
  ta.wma(src, length)  ta.hma(src, length)  ta.dema(src, length) ta.tema(src, length)
  ta.vwma(src, volume, length)  ta.stdev(src, length) [POPULATION]  ta.variance(src, length)
  ta.highest(src, length)  ta.lowest(src, length)  ta.highestbars(src, length)  ta.lowestbars(src, length)
  ta.sum(src, length)  ta.median(src, length)  ta.percentrank(src, length)  ta.linreg(src, length, offset=0)
  ta.change(src, length=1)  ta.mom(src, length)  ta.roc(src, length)  ta.shift(src, bars=1) [bars>=0 only]
  ta.tr(high, low, close)  ta.atr(high, low, close, length)  ta.rsi(src, length)  ta.cum(src)
  ta.crossover(a, b)  ta.crossunder(a, b)  [lists of bool]  ta.barssince(cond)  ta.valuewhen(cond, src, occurrence=0)
  ta.nz(x_or_list, replacement=0.0)
Moving averages and stdev skip a leading run of None, so ta.ema(ta.rsi(c, 14), 9) works.
Also available: `math` (math.sqrt, math.log, math.exp, math.pi, ...), builtins abs/min/max/sum/len/range/zip/enumerate/round/sorted/float/int/bool/list/dict."""

CONTRACT = """\
Write Python defining exactly one entry point:

    def compute(bars, params):
        ...
        return {"<line_key>": [float or None] * bars["n"], ...}

bars: dict of equal-length lists "open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4",
      "time" (epoch seconds UTC, bar open), plus "n" (bar count), "interval" ("1m".."1mo"),
      "tz_offset" (seconds east of UTC of the instrument's session clock).
params: dict with every input key from your spec, already typed and range-clamped.

Hard rules — the validator enforces each one and will send failures back:
- CAUSAL. The value at bar i may read bars 0..i only. Never index i+1, never centre a window,
  never shift forward, never repaint past bars (a pivot confirmed k bars later is plotted on the
  confirming bar, not back-dated). The validator re-runs on every prefix and diffs.
- Return EVERY line declared in spec.lines and no others; each list exactly bars["n"] long.
- Values are float/int or None (None = no value: warm-up, or no signal on a marker line).
  Never NaN or inf. Guard every division: flat bars have high == low, and volume can be 0.
- Must not crash on 8 bars, on a dead-flat tape, on a 25% gap, or on any input at its min/max.
- No imports, no classes, no global/nonlocal, no names or attributes starting with "_",
  no str.format (f-strings are fine), no print/open/eval. Top level: functions and constants only.
- O(n * length) is fine; avoid O(n^2) over the whole series (charts send up to 20,000 bars).
- Reference sources via params when the spec has a "source" input: bars[params["source"]].
- Prefer ta.* over hand-written loops: they ARE the chart's native implementations, so a study
  built on ta.rma/ta.stdev agrees with the native RSI/Bollinger to the last digit."""

SPEC_RULES = """\
Spec rules:
- pane "overlay" ONLY when every line is in price units (bands, MAs, stops, levels); otherwise "own".
- bounds [lo, hi] only when the math guarantees it (e.g. 0..100); else null. levels: the reference
  lines the indicator is conventionally read against (e.g. [70, 30], [0], [60, -60]); [] when none.
  A constant reference line is ALWAYS a level, never a line in spec.lines — the chart draws
  levels as dashed guides the way it does for its native oscillators.
- lines: key (snake_case), label (what the legend shows), plot one of line|stepline|area|columns|
  circles. Histograms → ONE columns line: the chart draws it as its native four-colour histogram
  (above/below zero x rising/falling, exactly LazyBear's/TradingView's MACD scheme), so never split
  a histogram into colour-bucket lines. Discrete markers/signals → circles with sparse=true and
  None where there is no signal (one sparse line per marker colour is fine). Name band lines upper/middle/lower and signal lines signal so they take the
  chart's native colours.
- inputs: every tunable number (key "length", never "period"), with honest min/max; type int|float|
  bool|enum|source. A "source" input lets the user pick close/open/high/low/hl2/hlc3/ohlc4.
- needs_volume true if the math reads volume.
- classification: as scoped, unless the code you write departs from the published definition.
- reference: an INDEPENDENT implementation the validator compares you with — kind "native" (a
  Charto indicator named in the scope) or "pandas_ta" (the library function described in the
  context: name = function, params = its keywords set to YOUR defaults, plus input=<column> when a
  single-series function should read a column other than close, e.g. volume; line_map theirs =
  the exact output column name), else "none". Map only continuous lines that are the same quantity. If the
  library's convention genuinely differs from the published one you implemented, use "none" and
  say how in waiver — the user is shown it. waiver is "" when a reference is given."""


_LIB: str | None = None


def _library_names() -> str:
    """The installed library's indicator names, as data for the scope call to
    choose from — picking from a real list instead of recalling one."""
    global _LIB
    if _LIB is None:
        try:
            import pandas_ta_classic as pta
            _LIB = " ".join(sorted({f for fs in pta.Category.values() for f in fs
                                    if not f.startswith("cdl")}))
        except Exception:  # noqa: BLE001
            _LIB = "(not installed)"
    return _LIB


def _schema_brief() -> dict:
    kv = {"type": "object", "additionalProperties": False, "required": ["name", "value"],
          "properties": {"name": {"type": "string"}, "value": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False,
            "required": ["intent", "title", "classification", "standard_name", "summary",
                         "native_equivalent", "native_exact", "library_equivalent",
                         "definition", "defaults", "conventions", "decline_reason"],
            "properties": {
                "intent": {"type": "string", "enum": ["build", "decline"]},
                "title": {"type": "string"},
                "classification": {"type": "string", "enum": list(ci.CLASSES)},
                "standard_name": {"type": "string"},
                "summary": {"type": "string"},
                "native_equivalent": {"type": "string"},
                "native_exact": {"type": "boolean"},
                "library_equivalent": {"type": "string"},
                "definition": {"type": "string"},
                "defaults": {"type": "array", "items": kv},
                "conventions": {"type": "array", "items": {"type": "string"}},
                "decline_reason": {"type": "string"}}}


_SCALAR = {"type": ["number", "string", "boolean"]}


def _schema_code() -> dict:
    line = {"type": "object", "additionalProperties": False,
            "required": ["key", "label", "plot", "sparse"],
            "properties": {"key": {"type": "string"}, "label": {"type": "string"},
                           "plot": {"type": "string", "enum": list(ci.PLOTS)},
                           "sparse": {"type": "boolean"}}}
    inp = {"type": "object", "additionalProperties": False,
           "required": ["key", "label", "type", "default", "min", "max", "step", "options"],
           "properties": {"key": {"type": "string"}, "label": {"type": "string"},
                          "type": {"type": "string", "enum": list(ci.INPUT_TYPES)},
                          "default": _SCALAR,
                          "min": {"type": ["number", "null"]}, "max": {"type": ["number", "null"]},
                          "step": {"type": ["number", "null"]},
                          "options": {"type": "array", "items": {"type": "string"}}}}
    pair = {"type": "object", "additionalProperties": False, "required": ["key", "value"],
            "properties": {"key": {"type": "string"}, "value": _SCALAR}}
    lmap = {"type": "object", "additionalProperties": False, "required": ["ours", "theirs"],
            "properties": {"ours": {"type": "string"}, "theirs": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False,
            "required": ["title", "short", "description", "classification", "standard_name",
                         "formula", "pane", "bounds", "levels", "needs_volume", "lines",
                         "inputs", "assumptions", "reference", "code", "notes_for_user"],
            "properties": {
                "title": {"type": "string"}, "short": {"type": "string"},
                "description": {"type": "string"},
                "classification": {"type": "string", "enum": list(ci.CLASSES)},
                "standard_name": {"type": "string"},
                "formula": {"type": "string"},
                "pane": {"type": "string", "enum": ["overlay", "own"]},
                "bounds": {"type": ["array", "null"], "items": {"type": "number"}},
                "levels": {"type": "array", "items": {"type": "number"}},
                "needs_volume": {"type": "boolean"},
                "lines": {"type": "array", "items": line},
                "inputs": {"type": "array", "items": inp},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "reference": {"type": "object", "additionalProperties": False,
                              "required": ["kind", "name", "params", "line_map", "waiver"],
                              "properties": {
                                  "kind": {"type": "string", "enum": ["native", "pandas_ta", "none"]},
                                  "waiver": {"type": "string"},
                                  "name": {"type": "string"},
                                  "params": {"type": "array", "items": pair},
                                  "line_map": {"type": "array", "items": lmap}}},
                "code": {"type": "string"},
                "notes_for_user": {"type": "string"}}}


# ── one schema-bound call ─────────────────────────────────────────────────
def _call(llm, model: str, *, system: str, user: str, schema: dict, name: str,
          effort: str = "medium", web: bool = False, max_tokens: int = 8000) -> tuple[dict, list, dict]:
    """(parsed JSON, [{title, url}] citations, usage)."""
    payload = {
        "model": model,
        "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "text": {"format": {"type": "json_schema", "name": name, "schema": schema,
                            "strict": True}},
        "reasoning": {"effort": effort},
        "max_output_tokens": max_tokens,
    }
    if web:
        payload["tools"] = [{"type": "web_search_preview", "search_context_size": "medium"}]
        # the URLs each search actually returned and each page actually opened
        payload["include"] = ["web_search_call.action.sources"]
    data = llm(payload)
    text, cites, searched, opened, found = [], [], 0, [], []
    for item in data.get("output", []):
        if item.get("type") == "web_search_call":
            searched += 1
            act = item.get("action") or {}
            if act.get("type") == "open_page" and act.get("url"):
                opened.append(act["url"])
            for src in act.get("sources") or []:
                if src.get("url"):
                    found.append(src["url"])
        if item.get("type") != "message":
            continue
        for c in item.get("content", []):
            if c.get("type") == "output_text":
                text.append(c.get("text", ""))
                for a in c.get("annotations") or []:
                    if a.get("type") == "url_citation" and a.get("url"):
                        cites.append({"title": a.get("title") or a["url"], "url": a["url"]})
    raw = "".join(text).strip()
    if not raw:
        status = data.get("status")
        why = (data.get("incomplete_details") or {}).get("reason")
        raise RuntimeError(f"the model returned no answer ({status}{', ' + why if why else ''})")
    # Inline citations when the model gave them (schema-bound output often
    # has none); otherwise the pages it opened, then what the searches
    # returned. Every URL here came out of the search tool, none from memory.
    if not cites:
        pick = opened or found[:4]
        cites = [{"title": u.split("/")[2].removeprefix("www.") if "//" in u else u, "url": u}
                 for u in pick]
    seen, uniq = set(), []
    for c in cites:
        u = c["url"].split("#")[0]
        if u not in seen:
            seen.add(u)
            uniq.append({"title": c["title"], "url": c["url"]})
    usage = dict(data.get("usage") or {})
    usage["web_searches"] = searched
    return json.loads(raw), uniq, usage


def _spec_from(draft: dict, brief: dict, sources: list) -> dict:
    """The model's schema-shaped draft → the stored spec shape."""
    ref = draft.get("reference") or {}
    reference = None
    if ref.get("kind") in ("native", "pandas_ta") and ref.get("line_map"):
        params = {p["key"]: p["value"] for p in ref.get("params") or []}
        reference = {"kind": ref["kind"],
                     "line_map": {m["ours"]: m["theirs"] for m in ref["line_map"]}}
        if ref["kind"] == "native":
            reference.update(name=ref.get("name"), params=params)
        else:
            reference.update(fn=ref.get("name"), kwargs=params)
    inputs = []
    for f in draft.get("inputs") or []:
        e = {"key": f["key"], "label": f["label"], "type": f["type"], "default": f["default"]}
        if f["type"] in ("int", "float"):
            e.update(min=f.get("min"), max=f.get("max"), step=f.get("step"))
            if f["type"] == "int":
                for k in ("default", "min", "max"):
                    if isinstance(e[k], float) and e[k].is_integer():
                        e[k] = int(e[k])
        if f["type"] == "enum":
            e["options"] = f.get("options") or []
        if f["type"] == "bool" and isinstance(e["default"], str):
            e["default"] = e["default"].lower() == "true"
        inputs.append(e)
    return {
        "title": draft["title"], "short": draft["short"], "description": draft["description"],
        "classification": draft["classification"],
        "standard_name": draft.get("standard_name") or brief.get("standard_name") or "",
        "formula": draft["formula"], "pane": draft["pane"],
        "bounds": draft.get("bounds") or None, "levels": draft.get("levels") or [],
        "needs_volume": bool(draft.get("needs_volume")),
        "lines": [{"key": ln["key"], "label": ln["label"], "plot": ln["plot"],
                   **({"sparse": True} if ln.get("sparse") else {})} for ln in draft["lines"]],
        "inputs": inputs, "assumptions": draft.get("assumptions") or [],
        "reference": reference, "sources": sources,
        "reference_waiver": "" if reference else (ref.get("waiver") or "").strip(),
        "library_equivalent": brief.get("library_verified") or "",
        "notes": draft.get("notes_for_user") or "",
    }


def _failures(report: dict) -> str:
    rows = [f"- {c['id']} ({c['label']}): {c['detail'] or 'failed'}"
            for c in report["checks"] if c["status"] == "fail" and c["blocking"]]
    return "\n".join(rows)


def build(request: str, *, llm, model: str, bars_provider, chart: dict,
          uid: int, edit: dict | None = None):
    """Yield {"type": "progress", stage, label, detail, data?} events, then one
    {"type": "final", "result": {...}}. Never raises: a failure is a result.

    `bars_provider()` → [(label, rows, interval, tz_offset)] of real data.
    `chart` = {"symbol", "interval"} of the chart the study is for.
    `edit` = the stored record when this is a change to an existing study.
    """
    t0 = time.perf_counter()
    usage_total = {"input_tokens": 0, "output_tokens": 0, "web_searches": 0}

    def acc(u):
        for k in usage_total:
            usage_total[k] += int(u.get(k) or 0)

    def ev(stage, label, detail="", **data):
        return {"type": "progress", "stage": stage, "label": label, "detail": detail,
                **({"data": data} if data else {})}

    def final(**result):
        result.setdefault("elapsed_s", round(time.perf_counter() - t0, 1))
        result.setdefault("usage", usage_total)
        return {"type": "final", "result": result}

    try:
        # ── understanding + researching: one call ─────────────────────
        yield ev("understanding", "Understanding", request[:120])
        natives = ", ".join(sorted(native.SPECS))
        current = ""
        if edit:
            current = (f"\n\nThis EDITS the user's existing indicator \"{edit['spec']['title']}\" "
                       f"({edit['spec']['classification']}): {edit['spec']['formula']}")
        brief, sources, u = _call(
            llm, model, name="scope", effort="low", web=True, max_tokens=4000,
            schema=_schema_brief(),
            system=("You scope requests for a charting platform's custom-indicator builder. "
                    "If the request names a published indicator and you are not certain of its "
                    "exact definition and conventions (smoothing, seeding, sample vs population "
                    "stdev, defaults), search the web first — prefer the original author, the "
                    "published script, TradingView or StockCharts references, TA-Lib. For the "
                    "user's own rule, do not search. "
                    "classification: 'standard' = a published, named indicator (textbook, original "
                    "author, or a widely used published script such as LazyBear's) built to its "
                    "published definition; 'variant' = one changed by the user; 'custom' = the "
                    "user's own unpublished method. "
                    f"Charto's native indicators: {natives}. native_equivalent = the matching one "
                    "or ''; native_exact = the request asks for nothing beyond it and its settings. "
                    "library_equivalent = the pandas-ta-classic function implementing the same "
                    "indicator, or '' if none. definition = the step-by-step computation with "
                    "every convention; defaults = its standard parameter values. "
                    "intent 'decline' only if this cannot be computed from a price/volume series "
                    "(orders, advice, order book, fundamentals, news)."),
            user=(f"Request: {request}{current}\n\npandas-ta-classic functions (for "
                  f"library_equivalent): {_library_names()}"))
        acc(u)
        if brief["intent"] == "decline":
            return (yield final(ok=False, declined=True, reason=brief["decline_reason"]))
        if brief["native_exact"] and brief["native_equivalent"] in native.SPECS and not edit:
            return (yield final(ok=False, native=brief["native_equivalent"],
                                reason=(f"{brief['title']} is already a native indicator "
                                        f"('{brief['native_equivalent']}') — add that instead "
                                        "of generating a copy.")))
        kind = {"standard": "standard indicator", "variant": "variant of a standard indicator",
                "custom": "custom methodology"}[brief["classification"]]
        yield ev("understanding", "Understood", f"{brief['title']} — {kind}",
                 title=brief["title"], classification=brief["classification"],
                 summary=brief["summary"])
        if u.get("web_searches"):
            yield ev("researching", "Researched",
                     f"{len(sources)} source{'s' if len(sources) != 1 else ''}",
                     sources=sources[:6])

        # the model's named library implementation, VERIFIED and described by
        # the library itself — real keywords and column names, not recalled ones
        real = bars_provider()
        sample = next((r[1] for r in real if len(r[1]) >= 60), [])
        probe = (ci.library_probe(brief["library_equivalent"], sample)
                 if brief.get("library_equivalent") else None)
        brief["library_verified"] = probe["fn"] if probe else ""

        # ── coding → testing → repairing ──────────────────────────────
        system = ("You implement technical indicators for Charto, an Indian charting platform, "
                  "as sandboxed Python. Correctness is the product: implement the scoped "
                  "definition exactly, state every assumption, and never present a custom "
                  "method as a standard one.\n\n" + CONTRACT + "\n\n" + TA_DOC + "\n\n"
                  + SPEC_RULES)
        context = [f"User request: {request}",
                   f"Scope (researched): {json.dumps({k: v for k, v in brief.items() if k not in ('intent', 'decline_reason', 'native_exact', 'library_verified')})}",
                   f"Chart: {chart.get('symbol')} on {chart.get('interval')}"]
        if probe:
            context.append(f"Independent implementation available — pandas-ta-classic: {json.dumps(probe)}")
        if brief.get("native_equivalent") in native.SPECS:
            nat = brief["native_equivalent"]
            try:
                nat_lines = list(native.compute(nat, sample)["lines"])
            except Exception:  # noqa: BLE001 — too few bars, or a volume study on no volume
                nat_lines = []
            context.append(f"Native reference available: '{nat}' (lines {nat_lines}, inputs "
                           f"{[f['key'] for f in native.inputs(nat)]})")
        if edit:
            context.append("Current version to modify (keep what the user did not ask to change, "
                           "keep the same line keys where the meaning is unchanged):\n"
                           f"spec: {json.dumps(edit['spec'])}\ncode:\n{edit['code']}")
        draft, spec, report, prev_fail = None, None, None, ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt == 1:
                yield ev("coding", "Writing code", "spec, inputs and compute()")
                user = "\n\n".join(context)
            else:
                yield ev("repairing", "Fixing", f"attempt {attempt} of {MAX_ATTEMPTS}: "
                         + ", ".join(c["id"] for c in report["checks"]
                                     if c["status"] == "fail" and c["blocking"]))
                user = ("\n\n".join(context)
                        + f"\n\nYour previous draft FAILED validation. Previous spec+code:\n"
                        + json.dumps({k: v for k, v in draft.items()})
                        + f"\n\nFailing checks (fix every one; keep everything that passed):\n"
                        + prev_fail)
            draft, _, u = _call(llm, model, name="indicator", effort="medium",
                                max_tokens=16000, schema=_schema_code(),
                                system=system, user=user)
            acc(u)
            spec = _spec_from(draft, brief, sources)
            yield ev("testing", "Testing",
                     f"{len(spec['lines'])} line{'s' if len(spec['lines']) != 1 else ''}, "
                     f"{len(spec['inputs'])} input{'s' if len(spec['inputs']) != 1 else ''} "
                     f"on {sum(1 for r in real if len(r[1]) >= 30)} real series + synthetic regimes")
            notes: list[str] = []
            report = ci.validate(spec, draft["code"], real, progress=notes.append)
            yield ev("testing", "Tested", report["summary"],
                     checks=[{"id": c["id"], "status": c["status"],
                              **({"detail": c["detail"][:240]} if c["status"] == "fail" else {})}
                             for c in report["checks"]])
            if report["passed"]:
                break
            prev_fail = _failures(report)

        # ── store, then render ────────────────────────────────────────
        report["attempts"] = attempt
        rec = ci.save(uid, spec=spec, code=draft["code"], report=report, prompt=request,
                      cid=edit["id"] if edit else None)
        if not report["passed"]:
            return (yield final(ok=False, failed_validation=True, id=rec["id"],
                                kept_previous=bool(rec.get("last_attempt_failed")),
                                title=spec["title"], report=report, spec=spec,
                                attempts=attempt))
        yield ev("rendering", "Adding to chart",
                 f"{spec['short']} on {chart.get('symbol')} · {chart.get('interval')}")
        return (yield final(ok=True, id=rec["id"], version=rec["version"], record=rec,
                            report=report, attempts=attempt))
    except Exception as exc:  # noqa: BLE001 — every failure becomes an honest result
        return (yield final(ok=False, error=f"{type(exc).__name__}: {exc}"))
