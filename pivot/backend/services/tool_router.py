"""Per-hop tool surface + stable cache key.

The model sees EVERY LLM-visible tool on every turn (the guarantee/
interpret line, 2026-07-16): tool SELECTION is language understanding,
which the model does natively and generalizes to phrasings no rule
anticipated — the prior ~40-regex keyword router was the second-largest
source of failures (misroutes whenever the right tool wasn't offered)
and, measured live, its misses tripled hops via the find_tool lazy-load
rescue and busted the prompt cache with rotating per-route key sets.
A single byte-stable toolset prefix-caches instead, so its marginal
token cost after the first turn is near zero.

What code still owns here (guarantees, not interpretation):
  - `select_prompt_modules` — additive instruction packs per intent
    (content injection, not tool hiding).
  - `cache_key_for` — now constant, since the toolset is constant.
Side-effect gates (alert refusals, no-trade markers, forced scopes)
live in chat_service / the tool layer and are unaffected.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional


# Tools that are ALWAYS in scope. The agent-builder, the four macro
# variants, and the clarification tool need to be available regardless
# of what the user typed — they're the escape hatches the model relies
# on. Including the macros up-front means a "buy 5 NIFTYBEES every
# weekday" prompt sees them even if the keyword router didn't classify
# the message as agent-y.
#
# `propose_workflow` is included now that its LLM-facing schema was
# collapsed from a 41-branch oneOf discriminated union into a flat
# `{step_type: enum, config: object}` shape (see
# `backend/agents/tools.py::_build_propose_workflow_schema`). Full tool
# object dropped from ~39,955 B (~9,988 tok) to ~7,362 B (~1,840 tok),
# so the cost of unconditional inclusion is ~1.8k tokens/turn — small
# enough to justify removing the route-misclassification risk where a
# multi-step prompt missed every keyword rule. Server-side Pydantic
# models in `workflows/schemas.py` still validate each step's config,
# so the trim does not weaken safety. The keyword rules below still
# mention `propose_workflow` for clarity; the redundancy is harmless.
def select_tool_names(message: str) -> Optional[set[str]]:
    """Return the FULL set of LLM-visible tool names, every turn.

    The model does tool selection itself — no keyword narrowing. Only
    `find_tool` is excluded: it was the lazy-load rescue hatch for
    router misses, pointless when nothing is hidden (its BM25 index
    remains available to non-chat callers).

    `ASK_USER` is synthetic — the chat service appends its ToolDef
    separately, so its absence from the registry set is expected.
    """
    from backend.services.tool_registry import get_tool_schema  # lazy: avoids import cycle

    names = {
        (d.get("function") or {}).get("name")
        for d in get_tool_schema()
    }
    names.discard(None)
    names.discard("find_tool")
    return names


# ── Intent packs (system_core.md + modules/*.md) ───────────────────
#
# Mirror of the tool-router idea for INSTRUCTIONS: system_core.md is always
# loaded; these per-intent packs are injected only when the turn matches.
# Same mechanism as select_tool_names — pure regex, microseconds, no LLM.
# A module name maps 1:1 to prompts/modules/<name>.md. Overlap is fine and
# expected (a rate-cut hedge with options loads options+hedge+thematic);
# the packs are small and de-duped by the loader.
_MODULE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(option|options|call option|put option|strikes?|expir(y|ies|ation)|"
                r"straddles?|strangles?|iron ?condor|condor|butterfly|vertical spread|"
                r"call spread|put spread|bull (call|put)|bear (call|put)|covered call|"
                r"protective put|greeks?|max ?pain|\bpcr\b|open interest|f\s*&\s*o|\bfno\b|"
                r"f and o)\b"), "options"),
    (re.compile(r"\b(backtest|back[- ]test|simulate|simulation)\b|"
                r"how would .* have (done|performed|fared)|what if i (had|bought|invested)|"
                r"historical(ly)? .* (return|perform)"), "backtest"),
    (re.compile(r"\b(baskets?|allocate|allocation|diversif\w*|equal[- ]?weight|"
                r"risk[- ]?parity|min[- ]?variance|rebalanc\w*|portfolio of|"
                r"split .* across)\b"
                # Construction verbs (Wave C): a build/design/create/make of a
                # strategy/basket/portfolio/allocation pulls the baskets pack so
                # the build_strategy doctrine + anti-bland invariants are in scope.
                r"|\b(build|make|create|design|construct|put together|give me)\b"
                r"[^.]{0,40}\b(strateg(y|ies)|basket|portfolio|allocation)\b"
                # Factor-tilt construction ("strategy that benefits from momentum").
                r"|\b(momentum|low[- ]?vol\w*|min[- ]?vol|quality|value)\b"
                r"[^.]{0,30}\b(factor|tilt|strateg(y|ies)|basket|portfolio)\b"),
     "baskets"),
    (re.compile(r"\b(thematic|monsoon|drought|rural|rate[- ]cut|rupee|depreciat\w*|"
                r"crude spike|defen[cs]e stocks?|manufacturing upcycle|"
                r"structural (story|trend|theme)|macro (scenario|theme))\b|"
                r"\b(sector|stocks?|theme) .* (will|going to|should) (do well|benefit|outperform)|"
                # expression intent on a theme/sector/scenario ("give me a way
                # to play the EV theme", "bet on the defence story")
                r"\b(play|bet on|express|position for|profit from|ride|capitali[sz]e on|"
                r"way to (play|bet)|how (do|should) i (play|bet|position))\b"
                r".{0,45}\b(theme|story|sector|space|trend|supply ?chain|scenario|upcycle|boom|move)\b|"
                # theme noun + expression word
                r"\b(ev|electric vehicle|battery|semiconductor|chips?|renewable|solar|"
                r"clean energy|hydrogen|artificial intelligence|manufacturing|defen[cs]e|"
                r"infra(structure)?)\b.{0,30}\b(theme|story|supply ?chain|upcycle|boom|space|play|basket)\b|"
                # macro/price thesis + 'what should I do' expression ask
                r"\b(crude|oil|gold|silver|rupee|dollar|inflation|interest rates?|war|conflict|tension)"
                r"\b.{0,70}\b(spike|surge|rally|crash|weaken|strengthen|going to|will|likely)\b"
                r".{0,60}\b(what should i|how (do|should) i|help me|position|benefit|play|hedge)\b|"
                # growth-story / structural-narrative phrasings (F8): a stated
                # growth story, consumption/capex/rural theme, or supercycle is a
                # THEMATIC construction ask → must run DISCOVER→VET→JUDGE→BUILD.
                r"\b(growth story|consumption (story|growth|theme|play)|"
                r"capex\s+(cycle|upcycle|boom|story)|super[- ]?cycle|"
                r"rural (recovery|revival)|import substitution|make in india|"
                r"production[- ]linked|\bpli\b)\b|"
                # a growth-story SECTOR noun + a story/play/theme/supply-chain
                # expression word (excludes bare commodity/plain-sector nouns so
                # a plain 'steel basket' or a bare price question stays out).
                r"\b(ev|electric vehicles?|semiconductors?|chips?|defen[cs]e|"
                r"renewables?|solar|hydrogen|manufacturing|infra(structure)?|"
                r"consumption|capex|5g|railways?|electronics?|batter(y|ies)|"
                r"clean energy|data ?cent(re|er)s?)\b"
                r"[^.]{0,45}\b(story|theme|play|super[- ]?cycle|upcycle|"
                r"supply ?chain|boom|space|basket|portfolio|strateg(y|ies))\b"),
     "thematic"),
    (re.compile(r"\bhedg\w+\b|downside protection|protect (my|the|this) .*"
                r"(position|holding|portfolio|downside)|insure (my|the)"), "hedge"),
    (re.compile(r"\bstop[- ]?loss(es)?\b|\bstoploss\b|trailing stop|trail\w* .*"
                r"(below|stop|%|percent)|protective stop"), "stoploss"),
    (re.compile(r"\bwebhooks?\b|callback url|post to (a|an|my) (url|endpoint|webhook)|"
                r"\b(slack|discord|telegram)\b|(notify|ping|send) .* (url|webhook|endpoint)"),
     "webhook"),
]


def select_prompt_modules(message: str, history_text: str = "") -> list[str]:
    """Return the ordered list of instruction-pack names to inject for this
    turn (empty when only the always-on core is needed).

    Matches the current message AND a short tail of recent conversation, so a
    follow-up like "make it 2 lots" mid-options-build still pulls the options
    pack. Order follows _MODULE_RULES so the injected block is deterministic.
    """
    hay = f"{message}\n{history_text}".lower()
    if not hay.strip():
        return []
    out: list[str] = []
    for pattern, name in _MODULE_RULES:
        if name not in out and pattern.search(hay):
            out.append(name)
    return out


def filter_registry_tools(
    all_tools: list[dict],
    selected: Optional[set[str]],
) -> list[dict]:
    """Filter the registry's tool-schema list down to `selected` names.

    `all_tools` is the OpenAI-shaped list returned by
    `tool_registry.get_tool_schema()`. Falls through to the full list
    when `selected` is None.
    """
    if selected is None:
        return all_tools
    out: list[dict] = []
    for defn in all_tools:
        fn = defn.get("function") or {}
        if fn.get("name") in selected:
            out.append(defn)
    return out


# ── Route-stable cache key ─────────────────────────────────────────


_CACHE_KEY_PREFIX = "pivot-chat-v2"


def cache_key_for(selected: Optional[set[str]]) -> str:
    """Build a deterministic prompt-cache key for this routed toolset.

    Why a per-route key matters: OpenAI's prompt cache is keyed by the
    *prefix bytes* of the request, scored against `prompt_cache_key`
    as a routing hint. When the visible toolset varies turn-to-turn
    (because the router narrows it based on user keywords), the
    system + tools prefix bytes differ, so a single global key
    misses on the first turn of every route.

    The fix: hash the sorted tool name list into a short tag and
    suffix the cache key with it. Each route signature now caches
    its own prefix; cache hits become turn-1 instead of turn-2.

    Returns a string like ``"pivot-chat-v2-fb1c83"``. The hash space
    is ample: 24 bits = 16M routes, vs ~50 plausible toolsets.

    Note on find_tool / lazy-load: callers MUST pass the *final* tool
    name set (router selection ∪ loaded_extras) on each hop. If
    `find_tool` surfaces e.g. `get_indicator` on hop N and we lazy-load
    it for hop N+1, the prompt cache key must differ from a hop that
    never called find_tool — otherwise the prefix bytes change but the
    OpenAI cache routing collapses two distinct surfaces into one slot.
    """
    if not selected:
        return f"{_CACHE_KEY_PREFIX}-all"
    # ASK_USER is synthetic and added downstream — exclude it from
    # the signature so its presence/absence doesn't shift the key.
    canonical = ",".join(sorted(n for n in selected if n != "ASK_USER"))
    sig = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"{_CACHE_KEY_PREFIX}-{sig}"
