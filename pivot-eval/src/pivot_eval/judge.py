"""Score a chatbot response against a per-case rubric.

Two-stage:

1. **Deterministic checks** — anything that can be verified without an LLM:
     - tool family was called
     - response length within ideal_length_words
     - obvious must_not violations (regex / keyword sweeps)

2. **Sarvam-as-judge** for irreducibly subjective items, with an anchored
   3-point scale (1=fail, 2=partial, 3=meets). Free-form 1-10 drifts; 1-3
   does not.

Aggregate verdict:
    pass     — every must_not == 3, every must_use_tool ✓, mean(should) ≥ 2
    partial  — must_not + must_use_tool ok but some should < 2
    fail     — any must_not < 3 OR any must_use_tool missing
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from typing import Iterable

import httpx

from .chatbot_client import ChatResponse, ToolCall
from .config import get_settings
from .dataset import TestCase, get_rubric


# Verdict labels.
PASS = "pass"
PARTIAL = "partial"
FAIL = "fail"


@dataclass
class CriterionScore:
    name: str                  # rubric key, normalised
    kind: str                  # 'must' | 'must_not' | 'must_use_tool' | 'should' | 'meta'
    score: int                 # 1..3 (3 = passes the criterion)
    rationale: str = ""
    deterministic: bool = False


@dataclass
class CaseScore:
    verdict: str               # pass | partial | fail
    criteria: list[CriterionScore] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---- Tool family mapping ------------------------------------------------
# Rubrics use loose names; our chat returns specific tool names. We treat
# the rubric name as a *family* and accept any concrete tool that fits.

_TOOL_FAMILY: dict[str, set[str]] = {
    "get_quote": {"get_live_price", "get_index_level", "get_ohlc"},
    "get_quote_or_equivalent": {"get_live_price", "get_index_level", "get_ohlc",
                                "run_compare"},
    "get_stock_metric_or_equivalent": {"get_live_price", "get_ohlc",
                                       "run_compare", "run_expression_screen"},
    "get_live_price": {"get_live_price"},
    "get_chart": {"run_compare"},
    "get_compare": {"run_compare"},
    "get_portfolio": {"get_portfolio_summary", "get_holdings",
                      "get_sector_breakdown"},
    "get_holdings": {"get_holdings", "get_portfolio_summary"},
    "run_backtest": {"run_backtest", "run_expression_backtest"},
    "run_screen": {"run_expression_screen"},
    "compare_yields": {"compare_yields"},
    "place_order": {"place_market_order", "place_limit_order",
                    "logiccard_emitted"},
    "any_tool": set(),  # special — see _tool_family_called
}


def _tool_family_called(family: str, tools: list[ToolCall]) -> bool:
    if not tools:
        return family == "none"
    if family == "any_tool":
        return True
    members = _TOOL_FAMILY.get(family)
    if members is None:
        # Unknown family — be lenient: any tool fired counts as a positive
        # signal, document via rationale.
        return bool(tools)
    return any(t.name in members for t in tools)


# ---- Deterministic checks -----------------------------------------------


_UNSOLICITED_ADVICE_RE = re.compile(
    r"\b("
    r"i\s+(?:would\s+)?recommend|consider\s+(?:buying|investing|adding)|"
    r"you\s+should\s+(?:buy|invest|add)|"
    r"great\s+opportunity\s+to|"
    r"strong\s+buy|definite(?:ly)?\s+a\s+buy|"
    r"check\s+out\s+our\s+(?:screener|strategy|product)|"
    r"have\s+you\s+considered\s+(?:buying|adding|investing)"
    r")\b",
    re.IGNORECASE,
)

_DEFINITION_RAMBLE_RE = re.compile(
    r"\b(?:price[- ]to[- ]earnings|p/e\s+ratio\s+is\s+a\s+(?:common|widely|"
    r"useful)|the\s+pe\s+ratio\s+(?:is|measures))",
    re.IGNORECASE,
)

_REFUSAL_RE = re.compile(
    r"\b(i\s+can(?:not|'t)|i\s+(?:am\s+)?unable|i\s+do\s+not\s+have\s+the\s+ability)\b",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _parse_length_range(spec) -> tuple[int, int] | None:
    """Accept '5-25', '<=30', '5'. Returns (low, high) or None."""
    if spec is None:
        return None
    s = str(spec).strip()
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^\s*<=\s*(\d+)\s*$", s)
    if m:
        return 0, int(m.group(1))
    m = re.match(r"^\s*(\d+)\s*$", s)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None


def _det_must_not(item: str, response_text: str, tools_called: list[ToolCall]) -> CriterionScore | None:
    """Deterministic checks for common must_not items. Returns None if not handled."""
    name = item.lower().strip()
    text = response_text or ""
    low = text.lower()

    if name in {"unsolicited_investment_advice", "unsolicited_advice",
                "stock_recommendations", "generic_marketing_pitch",
                "push_investing_topic"}:
        if _UNSOLICITED_ADVICE_RE.search(text):
            return CriterionScore(item, "must_not", 1, deterministic=True,
                                  rationale="matched advice/recommendation phrase")
        return CriterionScore(item, "must_not", 3, deterministic=True,
                              rationale="no advice/recommendation language")

    if name in {"hallucinate_value", "hallucinate_price", "fabricate_value",
                "fabricated_data"}:
        if tools_called:
            return CriterionScore(item, "must_not", 3, deterministic=True,
                                  rationale="value came from tool call")
        # No tool — look for specific numbers that imply data we don't have.
        if re.search(r"\b\d{2,5}\.\d{1,4}\b", text) and "<ltp>" not in low:
            return CriterionScore(item, "must_not", 1, deterministic=True,
                                  rationale="numeric value emitted without a tool call")
        return CriterionScore(item, "must_not", 3, deterministic=True,
                              rationale="no obvious fabricated value")

    if name in {"ramble_about_pe_definition", "ramble_about_definition",
                "lecture_about_concept", "explain_basics_unprompted"}:
        if _DEFINITION_RAMBLE_RE.search(text) or _word_count(text) > 80:
            return CriterionScore(item, "must_not", 1, deterministic=True,
                                  rationale="long explanation / definition leak")
        return CriterionScore(item, "must_not", 3, deterministic=True)

    if name in {"refuse_unnecessarily"}:
        if _REFUSAL_RE.search(text) and not tools_called:
            return CriterionScore(item, "must_not", 1, deterministic=True,
                                  rationale="bot refused without attempting a tool")
        return CriterionScore(item, "must_not", 3, deterministic=True)

    if name in {"ask_what_they_mean_unnecessarily"}:
        if text.rstrip().endswith("?") and len(text.split()) < 12:
            return CriterionScore(item, "must_not", 1, deterministic=True,
                                  rationale="short clarification-only response")
        return CriterionScore(item, "must_not", 3, deterministic=True)

    if name in {"give_generic_response", "generic_response"}:
        if re.search(r"\b(reliance|infosys|tcs|wipro|hdfc|sbi|nifty|sensex|"
                     r"banknifty|itc|maruti|bharti|airtel)\b",
                     text, re.IGNORECASE):
            return CriterionScore(item, "must_not", 3, deterministic=True)
        return None  # let the LLM judge handle nuanced cases

    if name in {"restate_full_capabilities", "restate_capabilities",
                "list_capabilities_again"}:
        if "execute orders on zerodha" in low and "capital protection" in low:
            return CriterionScore(item, "must_not", 1, deterministic=True,
                                  rationale="bot replayed the canonical capability list")
        return CriterionScore(item, "must_not", 3, deterministic=True)

    return None  # let the LLM judge handle anything else


def _det_should(name: str, response_text: str, user_input_lower: str) -> CriterionScore | None:
    """Deterministic checks for `should` / `must` items that are trivially verifiable."""
    n = name.lower().strip()
    text = response_text or ""

    if n in {"acknowledge_thanks_briefly", "acknowledge_thanks"}:
        if _THANKS_RE.search(user_input_lower) and _ACK_RE.search(text):
            return CriterionScore(name, "should", 3, deterministic=True,
                                  rationale="contains a thank-you acknowledgement phrase")
        if _THANKS_RE.search(user_input_lower):
            return CriterionScore(name, "should", 2, deterministic=True,
                                  rationale="user said thanks; no canonical acknowledgement detected")
        return None

    if n in {"greet_back", "greet"}:
        if _GREETING_RE.search(user_input_lower) and _GREETING_REPLY_RE.search(text):
            return CriterionScore(name, "should", 3, deterministic=True,
                                  rationale="contains a greeting reply")
        if _GREETING_RE.search(user_input_lower):
            return CriterionScore(name, "should", 2, deterministic=True,
                                  rationale="user greeted; reply doesn't contain a canonical greeting")
        return None

    if n in {"response_addresses_user_input"}:
        # Auto-pass when the response and the input share substantive content.
        # Heuristic: reply contains any meaningful word (>3 chars, non-stopword)
        # from the user's input.
        ulow = user_input_lower
        if _THANKS_RE.search(ulow) and _ACK_RE.search(text):
            return CriterionScore(name, "should", 3, deterministic=True,
                                  rationale="thanks → acknowledgement")
        if _GREETING_RE.search(ulow) and _GREETING_REPLY_RE.search(text):
            return CriterionScore(name, "should", 3, deterministic=True,
                                  rationale="greeting → greeting reply")
        return None

    return None




# ---- Sarvam judge -------------------------------------------------------


_JUDGE_SYSTEM = """\
You are a strict evaluator scoring chatbot responses against a rubric.

You return ONLY a JSON object — no prose, no markdown, no <think> blocks.
Each rubric item gets a 1, 2, or 3:
  1 = clearly fails the criterion
  2 = partially meets it
  3 = clearly meets it

Use 1 for any clear violation. Do not soften scores.

HARD CHECK (always present): `response_addresses_user_input`.
  1 = response is off-topic or doesn't address the user's input.
  3 = response directly addresses the user's input.
A response that doesn't address the user's input cannot pass regardless of
other dimensions.

Your output schema:
{"<rubric_item_key>": <1|2|3>, ..., "rationale": "<short>"}
Keys must match the rubric item names you were given exactly.
"""


def _judge_prompt(case: TestCase, response: ChatResponse, items: list[tuple[str, str]]) -> str:
    """Build the user prompt for Sarvam's judge call.

    For multi-turn cases we judge against the *final* user turn, since the
    rubric (`expected_behavior_final`) is by definition about the final
    response. Showing the joined transcript was confusing the judge — it
    invented "the user asked for both X and Y" misreadings.
    """
    rubric_pretty = "\n".join(f"- [{kind}] {name}" for name, kind in items)
    if case.is_multi_turn:
        final_user_turn = case.turns[-1]
        prior = " | ".join(case.turns[:-1]) or "(none)"
        framing = (
            f"PRIOR TURNS (context only, not the input under test): {prior}\n"
            f"FINAL USER TURN (this is what BOT RESPONSE must address): {final_user_turn}\n"
        )
    else:
        framing = f"USER INPUT: {case.input}\n"
    return (
        f"{framing}\n"
        f"BOT RESPONSE: {response.text}\n\n"
        f"TOOLS CALLED: {[t.name for t in response.tools_called] or 'none'}\n\n"
        f"RUBRIC ITEMS TO SCORE (return one int per name, plus 'rationale'):\n"
        f"{rubric_pretty}\n"
    )


def _call_sarvam_judge(case: TestCase, response: ChatResponse,
                       items: list[tuple[str, str]]) -> dict:
    """Single Sarvam call that scores every subjective item in one shot."""
    api_key = get_settings().sarvam_api_key
    if not api_key:
        # No key — judge defaults every subjective item to 2 (partial).
        return {name: 2 for name, _ in items} | {"rationale": "no SARVAM_API_KEY"}

    prompt = _judge_prompt(case, response, items)
    payload = {
        "model": "sarvam-m",
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 600,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                "https://api.sarvam.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
    except Exception as e:
        return {name: 2 for name, _ in items} | {"rationale": f"sarvam err: {e}"[:200]}

    # Strip <think> blocks defensively (closed and unclosed).
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    if "<think>" in content.lower() and "</think>" not in content.lower():
        # Unclosed <think> ... — drop everything from <think> on. Sarvam
        # truncates JSON output when the reasoning runs over budget.
        content = re.split(r"<think>", content, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    parsed = _coerce_json(content)
    if not isinstance(parsed, dict):
        # Judge default: 3 (clearly meets) for `must` items so judge unreliability
        # doesn't auto-fail a case the chatbot probably handled. Only the
        # deterministic checks should hard-fail.
        return {name: (3 if kind == "must" else 2) for name, kind in items} | {
            "rationale": f"judge unparseable: {content[:120]}",
        }

    # Clamp every value to 1..3 ints.
    out: dict = {"rationale": str(parsed.get("rationale", ""))[:300]}
    for name, _ in items:
        v = parsed.get(name)
        try:
            iv = int(v)
            out[name] = max(1, min(3, iv))
        except (TypeError, ValueError):
            out[name] = 2
    return out


def _coerce_json(text: str):
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    s = text.find("{")
    e = text.rfind("}")
    if s == -1 or e == -1 or e < s:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None


# ---- Public scoring -----------------------------------------------------


def _items_from_rubric(rubric: dict) -> list[tuple[str, str, object]]:
    """Flatten a rubric into (name, kind, value) tuples in deterministic order."""
    out: list[tuple[str, str, object]] = []
    for top_key, value in rubric.items():
        kind = _classify_kind(top_key)
        if kind in {"must", "must_not", "should"} and isinstance(value, list):
            for item in value:
                out.append((str(item), kind, None))
        else:
            out.append((str(top_key), kind, value))
    return out


def _classify_kind(key: str) -> str:
    k = key.lower()
    if k == "must_not": return "must_not"
    if k == "must_use_tool": return "must_use_tool"
    if k == "must": return "must"
    if k == "should": return "should"
    if k == "tone": return "should"
    if k.startswith("ideal_length") or k == "max_length_words": return "meta_length"
    return "meta"


_CANNED_GREETING_RE = re.compile(
    r"execute\s+orders\s+on\s+zerodha", re.IGNORECASE,
)
_PIVOT_PRODUCTS_RE = re.compile(
    r"\b(safegrow|earnmore|stormshield)\b", re.IGNORECASE,
)
_THANKS_RE = re.compile(
    r"^(thanks|thank\s+you|ty|thx|cheers|much\s+appreciated|appreciated)\b",
    re.IGNORECASE,
)
_ACK_RE = re.compile(
    r"\b(you'?re\s+welcome|anytime|no\s+problem|happy\s+to\s+help|"
    r"glad\s+to\s+help|sure\s+thing|of\s+course|my\s+pleasure)\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(
    r"^(hi|hii|hey|heyy|hello|yo|sup|hola|good\s+(?:morning|afternoon|evening))\b",
    re.IGNORECASE,
)
_GREETING_REPLY_RE = re.compile(
    r"\b(hi|hello|hey|hi\s+there|hey\s+there|good\s+(?:morning|afternoon|evening))\b",
    re.IGNORECASE,
)


def score(case: TestCase, response: ChatResponse) -> CaseScore:
    rubric = get_rubric(case)
    flattened = _items_from_rubric(rubric)
    criteria: list[CriterionScore] = []
    violations: list[str] = []
    notes: list[str] = []

    # ---- Hard auto-fail checks (added in v2 of the rubric) -------------
    text = response.text or ""
    user_input_lower = (case.input if not case.is_multi_turn
                        else case.turns[-1]).lower()

    # 1. Canned 4-line marketing pitch is an auto-fail anywhere.
    if _CANNED_GREETING_RE.search(text):
        criteria.append(CriterionScore(
            "auto_fail_canned_pitch", "must_not", 1, deterministic=True,
            rationale="response contains the canned 'Execute orders on Zerodha' pitch",
        ))
        violations.append("auto_fail_canned_pitch")

    # 2. Naming a Pivot product on a CASUAL input the user didn't initiate.
    if case.category == "CASUAL":
        user_text = (case.input if not case.is_multi_turn
                     else " ".join(case.turns)).lower()
        if (_PIVOT_PRODUCTS_RE.search(text)
                and not _PIVOT_PRODUCTS_RE.search(user_text)):
            criteria.append(CriterionScore(
                "auto_fail_unsolicited_product", "must_not", 1, deterministic=True,
                rationale="bot named a Pivot product on a casual input the user didn't ask about",
            ))
            violations.append("auto_fail_unsolicited_product")

    # Deterministic pass.
    needs_llm: list[tuple[str, str]] = []      # (name, kind) for items that must go to Sarvam
    for name, kind, value in flattened:

        if kind == "must_use_tool":
            family = str(value)
            if _tool_family_called(family, response.tools_called):
                criteria.append(CriterionScore(
                    f"must_use_tool:{family}", "must_use_tool", 3,
                    deterministic=True, rationale="tool family fired",
                ))
            else:
                tnames = [t.name for t in response.tools_called] or ["<none>"]
                criteria.append(CriterionScore(
                    f"must_use_tool:{family}", "must_use_tool", 1,
                    deterministic=True,
                    rationale=f"expected tool family '{family}', got {tnames}",
                ))
                violations.append(f"missing tool family: {family}")
            continue

        if kind == "meta_length":
            rng = _parse_length_range(value)
            wc = _word_count(response.text)
            if rng is None:
                criteria.append(CriterionScore(
                    f"length:{value}", "meta", 3, deterministic=True,
                    rationale=f"length spec unparseable; observed {wc} words",
                ))
                continue
            lo, hi = rng
            ok = lo <= wc <= hi
            criteria.append(CriterionScore(
                f"length:{lo}-{hi}", "meta", 3 if ok else (2 if wc <= hi * 1.5 else 1),
                deterministic=True,
                rationale=f"observed {wc} words (target {lo}-{hi})",
            ))
            if not ok and wc > hi * 1.5:
                notes.append(f"length {wc} far outside {lo}-{hi}")
            continue

        if kind == "must_not":
            det = _det_must_not(name, response.text, response.tools_called)
            if det is not None:
                criteria.append(det)
                if det.score < 3:
                    violations.append(f"must_not: {name}")
            else:
                needs_llm.append((name, "must_not"))
            continue

        if kind in {"must", "should"}:
            # Deterministic auto-pass for trivially-checkable casual rubrics.
            det = _det_should(name, text, user_input_lower)
            if det is not None:
                criteria.append(det)
                if det.score < 3:
                    if kind == "must":
                        violations.append(f"must: {name}")
                continue
            needs_llm.append((name, kind))
            continue

        # meta / unknown
        criteria.append(CriterionScore(
            name, "meta", 3, deterministic=True,
            rationale=f"informational rubric: {value!r}",
        ))

    # 3. Did the response actually address the user's input? Demoted from
    #    `must` to `should` because Sarvam's 1-3 scoring is too noisy to
    #    use as a hard gate — it routinely scored 2 on responses where its
    #    own rationale said "directly addresses the user's input".
    needs_llm.append(("response_addresses_user_input", "should"))

    # LLM judge for remaining items.
    if needs_llm:
        judged = _call_sarvam_judge(case, response, needs_llm)
        for name, kind in needs_llm:
            sc = int(judged.get(name, 2))
            criteria.append(CriterionScore(
                name, kind, sc, rationale=str(judged.get("rationale", ""))[:160],
            ))
            if kind == "must_not" and sc < 3:
                violations.append(f"must_not: {name}")
            if kind == "must" and sc < 3:
                violations.append(f"must: {name}")

    # Aggregate verdict.
    verdict = _aggregate(criteria)
    return CaseScore(verdict=verdict, criteria=criteria,
                     violations=violations, notes=notes)


def _aggregate(criteria: list[CriterionScore]) -> str:
    must_nots = [c for c in criteria if c.kind == "must_not"]
    musts = [c for c in criteria if c.kind == "must"]
    must_tools = [c for c in criteria if c.kind == "must_use_tool"]
    shoulds = [c for c in criteria if c.kind == "should"]

    if any(c.score < 3 for c in must_nots):
        return FAIL
    if any(c.score < 3 for c in must_tools):
        return FAIL
    if any(c.score < 3 for c in musts):
        return FAIL

    if not shoulds:
        return PASS
    avg = statistics.mean(c.score for c in shoulds)
    return PASS if avg >= 2.0 else PARTIAL
