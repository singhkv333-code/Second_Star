"""Shared contracts for the strategy-builder + dynamic-questions feature.

This module is the **single source of truth** that every downstream agent
codes against. It defines the wire shapes (Pydantic v2 models + ``Literal``
enums + constants) and the **public function signatures** (typed Protocols /
stubs, no bodies) for the three engines:

  * ``clarify_engine``  — dynamic, VOI-ranked clarifying questions (Workstream A).
  * ``weighting``       — weighting schemes incl. shrinkage covariance.
  * ``strategy_builder``— DB-driven equity+gold basket builder (Workstream B).

Authoritative spec: ``docs/plans/STRATEGY_BUILDER_AND_QUESTIONS_PLAN.md`` §2-3.

Scope of THIS phase (user-approved):
  * Workstream A — dynamic clarifying-questions, FULL.
  * Workstream B — creative builder limited to **EQUITY + GOLD** only:
    weighting schemes (equal / mcap / risk-parity / min-variance /
    black-litterman / factor), a fundamentals-DB selection gate
    (F-score / Magic-Formula / multi-factor), a sector cap + correlation
    check, and a gold (SGB/ETF) sleeve.
  * DEFERRED to a later phase (do NOT model here beyond the open enum slot):
    options/hedge sleeves and the full eval harness (Workstream C).

Cross-cutting contracts that DO NOT change:
  * **register-not-execute** — cards register/arm; the user places in their
    own broker app. Nothing here auto-executes.
  * **not-advice disclaimer** — every rendered strategy card carries
    ``disclaimer`` (default :data:`DEFAULT_DISCLAIMER`).

Open-decision defaults baked into these contracts (per the approved plan):
  * **Slot-state travels IN-BAND** through the chat turn — there is no new
    endpoint. :class:`ClarifyCard` carries ``session_slot_state`` so the FE
    round-trips it on the next user message; no separate persistence contract.
  * **Covariance uses shrinkage** with an **honest equal-weight fallback**
    when price history is too short (see :func:`Weighting.compute_weights`).
  * **Skipped slots take stated-assumption defaults** — every slot in
    :class:`SlotState` has a default and an ``assumed`` flag
    (:class:`SlotAssumptions`); the builder surfaces "(assumed …)" in the card.

Style: ``from __future__ import annotations``, Pydantic v2 (``ConfigDict``),
``Literal`` enums, strict typing throughout. No I/O at import time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover - typing-only imports, no runtime cost
    # ``ctx`` and ``price_history`` are intentionally loose here. Downstream
    # modules narrow them; the contract only requires "the per-turn context
    # object" and "a per-symbol OHLCV history provider". They are imported
    # under TYPE_CHECKING so this module stays import-light and I/O-free.
    from collections.abc import Mapping, Sequence


# ════════════════════════════════════════════════════════════════════════════
# Tool-name constants
# ════════════════════════════════════════════════════════════════════════════

ASK_USER_DYNAMIC: str = "ask_user_dynamic"
"""Synthetic tool the model calls to request dynamic clarifying questions.

The LLM does NOT author the questions field-by-field; it calls this with the
request context and the backend (``clarify_engine``) runs generate→rank→
validate→stop. Registered alongside ``ASK_USER`` in ``_ALWAYS_INCLUDE``."""

BUILD_STRATEGY: str = "build_strategy"
"""Tool the model calls to construct a DB-driven equity+gold basket.

Routes to ``strategy_builder.build_strategy`` (plan §3a Steps 1-5). Inputs are
the request + the filled :class:`SlotState`; output is a
:class:`StrategyBuilderCard`."""


# ════════════════════════════════════════════════════════════════════════════
# Render-hint strings (the FE dispatches on these under raw_data._render_hint)
# ════════════════════════════════════════════════════════════════════════════

RENDER_HINT_CLARIFY: str = "clarify_card"
"""``raw_data = {"_render_hint": "clarify_card", "clarify": <ClarifyCard>}``.

Emitted via the existing ask_user channel; ``needs_clarification=True`` pauses
the turn so it never reaches an executor (mirrors the ``ask_user`` hint sites
in chat_service.py)."""

RENDER_HINT_STRATEGY_BUILDER: str = "strategy_builder_card"
"""``raw_data = {"_render_hint": "strategy_builder_card", ...StrategyBuilderCard}``.

The builder card is editable (register-not-execute) and ends with the
not-advice disclaimer."""


# ════════════════════════════════════════════════════════════════════════════
# Constants & sensible defaults
# ════════════════════════════════════════════════════════════════════════════

DEFAULT_DISCLAIMER: str = (
    "This is analysis and a framework, not personalised financial advice. "
    "Pivot registers ideas; you confirm and place orders in your own broker app."
)
"""Mandatory footer on every :class:`StrategyBuilderCard`."""

MAX_CLARIFY_QUESTIONS: int = 5
"""Hard budget cap on questions surfaced per turn (plan §2c). The literature
caps at 3-4; we allow up to 5 and stop early via the VOI stopping rule."""

DEFAULT_SECTOR_CAP_PCT: float = 32.0
"""Default single-sector weight ceiling (% of equity sleeve), ~30-35% band
from plan §3a Step 1. Enforced as an anti-bland guardrail before render."""

MIN_HISTORY_BARS_FOR_COV: int = 120
"""Minimum per-symbol daily bars required to estimate a (shrinkage) covariance
matrix. Below this, covariance-based schemes (``risk_parity`` / ``min_variance``
/ ``black_litterman``) fall back to equal-weight with a stated reason —
the honest-boundary open-decision default."""


# ════════════════════════════════════════════════════════════════════════════
# Literal enums  (the closed vocabularies every layer agrees on)
# ════════════════════════════════════════════════════════════════════════════

# ── Slot vocabularies ───────────────────────────────────────────────────────

ViewDirection = Literal["bull", "bear", "neutral", "none"]
"""User's directional read. ``none`` = no expressed view (passive / "own it")."""

ViewTarget = Literal["stock", "sector", "index", "market"]
"""What the view is *about* — gates universe construction in builder Step 1."""

Conviction = Literal["low", "medium", "high"]
"""Strength of the view. Feeds Black-Litterman view confidence and tilt size."""

RiskLevel = Literal["conservative", "balanced", "aggressive"]
"""Risk appetite. Drives the weighting-scheme decision rule (plan §3a Step 2)
and the gold-ballast %."""

Horizon = Literal["tactical", "medium", "long"]
"""``tactical`` <1y · ``medium`` 1-5y · ``long`` 5y+. Influences gold sleeve
and (in a later phase) option expiries."""

AssetClass = Literal["equity", "etf_mf", "options", "gold"]
"""Allow/deny vocabulary for :class:`AssetPrefs`. ``options`` is accepted in
the contract but is DEFERRED in this phase's builder (equity+gold only)."""

# ── Builder vocabularies ────────────────────────────────────────────────────

WeightingScheme = Literal[
    "equal",
    "mcap",
    "risk_parity",
    "min_variance",
    "black_litterman",
    "factor",
    "conviction",
]
"""Named weighting schemes (plan §3a Step 2). ``risk_parity`` (ERC) is the
smart-default fallback, NOT ``equal`` — equal only survives for ≤4 names /
single asset class."""

SelectionGate = Literal["fscore", "magic_formula", "multifactor", "none"]
"""Fundamentals-DB selection gate (plan §3a Step 1). ``none`` is allowed ONLY
for pure price/technical baskets; the builder must still drop fundamentally
broken names when DB data exists."""

SleeveKind = Literal["gold", "options", "hedge"]
"""Sleeve discriminator. Only ``gold`` is built in this phase; ``options`` /
``hedge`` are reserved in the enum for the deferred phase so the FE union and
wire shape don't have to change later."""

GoldInstrumentKind = Literal["sgb", "etf"]
"""Gold sleeve instruments — Sovereign Gold Bond (long core) + Gold ETF
(liquid). MCX gold futures are tradeable separately (register-not-execute);
this gold sleeve uses ETF/SGB by design."""


# ════════════════════════════════════════════════════════════════════════════
# Slot-state models  (Workstream A fills these; Workstream B consumes them)
# ════════════════════════════════════════════════════════════════════════════


class ViewSlot(BaseModel):
    """The user's market view: direction × target × conviction.

    ``direction='none'`` means no expressed view (the builder treats it as a
    passive / own-the-market signal rather than a directional bet)."""

    model_config = ConfigDict(extra="forbid")

    direction: ViewDirection = "none"
    target: ViewTarget = "market"
    conviction: Conviction = "medium"


class AssetPrefs(BaseModel):
    """Which asset classes the user will / won't hold, plus exclusions.

    ``allow`` / ``deny`` are :data:`AssetClass` lists; ``exclusions`` is a free
    list of qualitative carve-outs (sectors, ``"PSU"``, ``"ESG"`` themes, named
    tickers) that the selection gate must honour."""

    model_config = ConfigDict(extra="forbid")

    allow: list[AssetClass] = Field(default_factory=lambda: ["equity", "etf_mf", "gold"])
    deny: list[AssetClass] = Field(default_factory=lambda: ["options"])
    exclusions: list[str] = Field(default_factory=list)


class SlotAssumptions(BaseModel):
    """Per-slot ``assumed`` flags.

    A slot is ``assumed`` when its value came from a default (the user skipped
    the question or it was never asked) rather than from an explicit answer or
    a confident parse of the request. The builder surfaces every assumed slot
    as "(assumed …)" in :attr:`StrategyBuilderCard.assumptions`, never blocking
    the build (plan §2f / §3c)."""

    model_config = ConfigDict(extra="forbid")

    view: bool = True
    risk: bool = True
    horizon: bool = True
    capital_inr: bool = True
    asset_prefs: bool = True
    theme: bool = True


class SlotState(BaseModel):
    """The full strategy-build slot-state — the in-band contract between the
    clarify engine and the builder.

    Travels INSIDE the chat turn (no new endpoint): the clarify card embeds it
    as :attr:`ClarifyCard.session_slot_state`; the FE round-trips it on the next
    user message; ``chat_service`` merges each answer and re-checks the stopping
    rule. When the rule fires, the filled ``SlotState`` is handed to
    :func:`StrategyBuilder.build_strategy`.

    Every slot has a sensible default so a fully-skipped flow still builds. Use
    :meth:`mark_assumed` to flag which slots are running on defaults."""

    model_config = ConfigDict(extra="forbid")

    view: ViewSlot = Field(default_factory=ViewSlot)
    risk: RiskLevel = "balanced"
    horizon: Horizon = "medium"
    capital_inr: Optional[float] = None
    """Investable capital in ₹. Gates #names, lot rounding, and SGB tickets.
    ``None`` ⇒ the builder sizes in percentages and states the assumption."""

    asset_prefs: AssetPrefs = Field(default_factory=AssetPrefs)
    theme: Optional[str] = None
    """Optional thematic tilt ("quality compounders", "rate-cut beneficiaries")
    resolved against ``thematic_map`` in builder Step 1."""

    symbols: Optional[list[str]] = None
    """Explicit NSE constituents to **pin** the universe to — the vetted winners
    from the DISCOVER→VET→JUDGE thematic flow (plan §3 B1 / thematic.md §5). When
    set, the builder skips discovery and builds *exactly* these names: fundamentals
    are still fetched for the per-name ``gate_metrics`` display (missing → shown
    honestly as no-data, **never dropped**), the weighting scheme + sizing are
    still computed, and the sector cap becomes advisory (warn, don't trim) because
    the caller/flow already chose the names. ``None`` ⇒ the builder discovers the
    universe from theme/sector as before. Travels in-band with the rest of the
    slot-state so a clarify round-trip preserves the pin."""

    assumed: SlotAssumptions = Field(default_factory=SlotAssumptions)
    """Which slots are running on defaults (vs. explicitly set)."""

    def mark_assumed(self, *slots: str, value: bool = True) -> SlotState:
        """Mark the named slots as ``assumed`` (or not) in place; return self.

        Call this when a slot is filled from a default because the user skipped
        the question, or cleared (``value=False``) once a real answer arrives.

        Valid slot names are the fields of :class:`SlotAssumptions`:
        ``view``, ``risk``, ``horizon``, ``capital_inr``, ``asset_prefs``,
        ``theme``. Unknown names raise ``ValueError`` so typos fail loudly."""
        valid = set(SlotAssumptions.model_fields)
        for slot in slots:
            if slot not in valid:
                raise ValueError(
                    f"unknown slot {slot!r}; valid slots are {sorted(valid)}"
                )
            setattr(self.assumed, slot, value)
        return self


# ════════════════════════════════════════════════════════════════════════════
# Clarify payload models  (serialized under _render_hint == "clarify_card")
# ════════════════════════════════════════════════════════════════════════════


class ClarifyOption(BaseModel):
    """One answer chip for a clarifying question.

    ``id`` is the stable machine value the FE echoes back (e.g. ``"bull"``);
    ``label`` is the human-facing chip text. Options are MECE and grounded in
    the request's real instruments/structures (plan §2b)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str


class ClarifyQuestion(BaseModel):
    """A single VOI-ranked clarifying question.

    ``slot`` is the :class:`SlotState` field this question fills (e.g.
    ``"view"``, ``"risk"``). ``voi`` is the decision-relevance score
    ``StrategyEIG(q) − λ·BurdenCost(q)`` (plan §2a) used to rank and prune.
    ``free_text=True`` renders a "Something else" affordance; ``skippable=True``
    renders "Skip" (which falls back to the slot default + flags it assumed)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    slot: str
    prompt: str
    voi: float
    options: list[ClarifyOption] = Field(default_factory=list)
    free_text: bool = True
    skippable: bool = True


class ClarifyCard(BaseModel):
    """The ``clarify_card`` payload — paginated "N of M" clarifying questions.

    Serialized as
    ``raw_data = {"_render_hint": "clarify_card", "clarify": <ClarifyCard>}``.

    ``session_slot_state`` is the current :class:`SlotState` (the in-band
    travelling state). ``total`` is M (question count), ``index`` is the 0-based
    current question (so the FE shows ``index+1`` of ``total``). ``questions``
    is the ranked, validated, ≤:data:`MAX_CLARIFY_QUESTIONS` list."""

    model_config = ConfigDict(extra="forbid")

    session_slot_state: SlotState
    total: int
    index: int = 0
    questions: list[ClarifyQuestion]


# ════════════════════════════════════════════════════════════════════════════
# Strategy-builder payload models
#   (serialized under _render_hint == "strategy_builder_card")
# ════════════════════════════════════════════════════════════════════════════


class StrategyConstituent(BaseModel):
    """One equity name in the basket, with the fundamentals that earned its slot.

    ``weight_pct`` is the constituent's share of the equity sleeve (the sleeves'
    ``pct`` values + the equity sleeve sum to 100). ``gate_metrics`` carries the
    concrete numbers the selection gate used (e.g. ``{"fscore": 8, "roe": 21.4,
    "earnings_yield": 0.071}``) so the card can *show its work* — anti-bland
    guardrail #2 ("never 'top mcap' alone")."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str
    sector: str
    weight_pct: float
    gate_metrics: dict[str, float] = Field(default_factory=dict)
    # WHY this name carries THIS weight — the causal/thematic hook or the
    # quality-gate rationale (e.g. "Direct beneficiary — solar/agri pumps" or
    # "Overweight: strongest gate (ROE 24%, low D/E)"). Populated by the builder
    # so the card shows differentiated, reasoned weights rather than a bare
    # equal split. Empty only when genuinely no rationale is available.
    weight_reason: str = ""


class GoldInstrument(BaseModel):
    """One instrument inside a gold sleeve (SGB or Gold ETF)."""

    model_config = ConfigDict(extra="forbid")

    kind: GoldInstrumentKind
    symbol: str
    name: str
    weight_pct: float
    """Share of the OVERALL portfolio (not of the sleeve), so it sums with the
    sleeve ``pct`` consistently."""


class Sleeve(BaseModel):
    """A non-core portfolio sleeve.

    In this phase only ``kind == "gold"`` is populated (SGB long core + Gold ETF
    liquid leg, 5-15% per the risk/horizon rule). ``options`` / ``hedge`` sleeve
    shapes are reserved for the deferred phase; their leg lists are intentionally
    left out of this contract to avoid prematurely freezing them — they will be
    added as ``legs`` when that phase lands."""

    model_config = ConfigDict(extra="forbid")

    kind: SleeveKind
    pct: float
    """Sleeve's share of the OVERALL portfolio."""

    instruments: list[GoldInstrument] = Field(default_factory=list)
    """Populated for the gold sleeve. Other sleeve kinds carry their own
    (deferred) structures and leave this empty."""

    note: Optional[str] = None
    """Optional one-line rationale for the sleeve ("inflation/rupee hedge")."""


class StrategyAlternative(BaseModel):
    """One "you might prefer this instead" alternative strategy.

    A deliberately tiny shape — ``title`` is the short label the FE shows as a
    pill/heading (e.g. "Value tilt", "Lower-risk", "Passive"); ``detail`` is the
    one-to-two plain-English sentences explaining what it changes and *when the
    user would prefer it*. Alternatives are suggestions, not selectable legs —
    the FE renders them as a labelled list, never as constituents."""

    model_config = ConfigDict(extra="forbid")

    title: str
    detail: str


class StrategyBuilderCard(BaseModel):
    """The ``strategy_builder_card`` payload — an editable, register-not-execute
    basket with a named scheme, a named gate, constituents, sleeves, and a
    rationale tying back to ``{view × risk × horizon × capital}``.

    Serialized as
    ``raw_data = {"_render_hint": "strategy_builder_card", ...this card...}``.

    Anti-bland invariants the builder asserts before producing this card
    (plan §3a): a real :attr:`weighting_scheme` (not bare equal unless ≤4
    names), a named :attr:`selection_gate`, an enforced :attr:`sector_cap`, a
    stated view mapped to structure, and honest boundaries when a sleeve is
    infeasible (recorded in :attr:`assumptions`)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    rationale: str
    """One-paragraph defence tying the structure back to the slot-state."""

    weighting_scheme: WeightingScheme
    selection_gate: SelectionGate
    sector_cap: float
    """The single-sector ceiling actually enforced (% of equity sleeve)."""

    constituents: list[StrategyConstituent]
    sleeves: list[Sleeve] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    """Human-readable "(assumed …)" lines for every defaulted/skipped slot and
    every honest-boundary fallback (e.g. covariance too thin → equal-weight)."""

    alternatives: list[StrategyAlternative] = Field(default_factory=list)
    """1-3 GENUINELY different strategies the user might prefer instead of the
    proposed basket, each as a ``{title, detail}`` pair (see
    :class:`StrategyAlternative`). The FE should render these as a small
    "Alternatives" section under the card — a labelled list of titles with the
    one-line ``detail`` beneath each — NOT as additional constituents. They are
    suggestions only; nothing here is selected or registered until the user
    re-asks for one of them."""

    capital_inr: Optional[float] = None
    """The investable capital the basket was sized against (₹), echoed from
    :attr:`SlotState.capital_inr` so the FE "Save as basket" action can persist it
    with the basket. ``None`` when the user stated no amount (the basket was sized
    in percentages)."""

    disclaimer: str = DEFAULT_DISCLAIMER


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC FUNCTION SIGNATURES  (typed Protocols, NO bodies — downstream impls)
# ════════════════════════════════════════════════════════════════════════════
#
# These Protocols pin the call shapes the three engines must satisfy. Each
# engine module (services/weighting.py, services/strategy_builder.py,
# services/clarify_engine.py) implements module-level functions matching the
# corresponding Protocol method (drop the ``self``). Protocols are used (rather
# than abstract bodies) so the contract is import-light, structurally checked,
# and free of premature implementation choices.


@runtime_checkable
class Weighting(Protocol):
    """Contract for ``services/weighting.py``."""

    def compute_weights(
        self,
        symbols: Sequence[str],
        scheme: WeightingScheme,
        *,
        price_history: Mapping[str, object],
        mcap: Optional[Mapping[str, float]] = None,
        views: Optional[Mapping[str, float]] = None,
    ) -> dict[str, float]:
        """Return normalised target weights (summing to 1.0) keyed by symbol.

        Schemes:
          * ``equal``           — 1/N.
          * ``mcap``            — market-cap proportional (needs ``mcap``).
          * ``risk_parity``     — equal risk contribution (ERC) from the
            **shrinkage** covariance of ``price_history``.
          * ``min_variance``    — global minimum-variance from shrinkage cov.
          * ``black_litterman`` — mcap prior + chat ``views`` (per-symbol
            expected-return tilts / confidences) as the BL view vector.
          * ``factor``          — factor-score weighting (quality/value from the
            fundamentals DB, momentum/low-vol from ``price_history``).

        Covariance policy (open-decision default): estimate with **shrinkage**
        (e.g. Ledoit-Wolf). When any symbol has fewer than
        :data:`MIN_HISTORY_BARS_FOR_COV` bars, FALL BACK to equal-weight and let
        the caller state the reason — never emit an unreliable covariance fit.

        Args:
            symbols: Ordered basket symbols (output preserves coverage of these).
            scheme: One of :data:`WeightingScheme`.
            price_history: ``{symbol -> OHLCV history}``; the covariance/momentum
                source. (Loose-typed in the contract; the impl narrows it.)
            mcap: ``{symbol -> market cap ₹cr}``; required for ``mcap`` and the
                ``black_litterman`` prior.
            views: ``{symbol -> tilt}`` from the parsed chat view; the BL view
                vector. Ignored by non-BL schemes.

        Returns:
            ``{symbol -> weight}`` with weights ≥ 0 summing to 1.0.
        """
        ...


@runtime_checkable
class StrategyBuilder(Protocol):
    """Contract for ``services/strategy_builder.py`` (equity + gold only)."""

    def build_strategy(
        self,
        request: str,
        slots: SlotState,
        ctx: object,
    ) -> StrategyBuilderCard:
        """Run the §3a construction pipeline and return a render-ready card.

        Steps (plan §3a, equity+gold scope):
          1. **Universe & selection** — build the candidate universe from
             theme/sector/index (``thematic_map`` / ``sector_universe``), then
             gate/rank on the fundamentals DB (``fundamentals_screen`` /
             ``screen_fundamentals``) via the chosen :data:`SelectionGate`.
             Enforce the sector cap + correlation check.
          2. **Weighting** — pick a :data:`WeightingScheme` by the decision
             rule (NOT a hardwired default) and call
             :meth:`Weighting.compute_weights`.
          3. **Macro structure** — barbell / core-satellite / focused as the
             request implies.
          4. **Sleeves** — gold (SGB + ETF) when conservative / long horizon /
             rupee-hedge intent and ``gold`` is allowed. (options/hedge sleeves
             are DEFERRED this phase.)
          5. **Sizing & feasibility** vs ``slots.capital_inr`` — round to
             feasible tickets; if something doesn't fit, state it and offer the
             nearest real structure (honest boundary).

        Asserts the anti-bland guardrails before returning. Skipped/assumed
        slots take defaults and are surfaced in
        :attr:`StrategyBuilderCard.assumptions`.

        Args:
            request: The raw user message that triggered the build.
            slots: The filled (or default-on-skip) :class:`SlotState`.
            ctx: The per-turn context (DB sessions, price/data providers,
                conversation id). Loose-typed in the contract; the impl narrows.

        Returns:
            A :class:`StrategyBuilderCard` (register-not-execute, disclaimer set).
        """
        ...


@runtime_checkable
class ClarifyEngine(Protocol):
    """Contract for ``services/clarify_engine.py`` (Workstream A)."""

    def should_ask(self, request: str, slots: SlotState, ctx: object) -> bool:
        """The **skip-entirely gate** (plan §2c) — run FIRST.

        Return ``False`` (build directly, ask nothing) when the request is
        already specific: top strategy-candidate confidence ≥ ``τ_high`` OR the
        margin between the top-2 candidate structures > ``m`` (reusing the
        intent-confidence signals computed at routing time). Return ``True``
        only when clarifying could materially change the build."""
        ...

    def generate_clarify_card(
        self,
        request: str,
        slots: SlotState,
        ctx: object,
    ) -> Optional[ClarifyCard]:
        """Generate→rank→validate→stop and return a :class:`ClarifyCard`.

        Pipeline (plan §2b): slot-infer → emit ~8-10 grounded candidate
        questions → VOI rank (``StrategyEIG − λ·Burden``) → MECE/grounding
        validate + de-dup → stopping rule (≤:data:`MAX_CLARIFY_QUESTIONS`).

        Returns ``None`` when nothing is worth asking (equivalent to
        ``should_ask() is False`` / all candidates pruned below ``τ_q``) — the
        caller should then build directly. Otherwise returns a card whose
        ``session_slot_state`` carries the current (partially-filled) slots so
        the FE can round-trip it in-band.

        Args:
            request: The raw user message.
            slots: The slot-state inferred/filled so far this conversation.
            ctx: Per-turn context (for the LLM call + read-only
                ``screen_fundamentals``/``sector_universe`` peek to ground
                options). Loose-typed; the impl narrows.

        Returns:
            A :class:`ClarifyCard`, or ``None`` to skip clarification entirely.
        """
        ...


__all__ = [
    # tool names
    "ASK_USER_DYNAMIC",
    "BUILD_STRATEGY",
    # render hints
    "RENDER_HINT_CLARIFY",
    "RENDER_HINT_STRATEGY_BUILDER",
    # constants
    "DEFAULT_DISCLAIMER",
    "MAX_CLARIFY_QUESTIONS",
    "DEFAULT_SECTOR_CAP_PCT",
    "MIN_HISTORY_BARS_FOR_COV",
    # enums
    "ViewDirection",
    "ViewTarget",
    "Conviction",
    "RiskLevel",
    "Horizon",
    "AssetClass",
    "WeightingScheme",
    "SelectionGate",
    "SleeveKind",
    "GoldInstrumentKind",
    # slot-state
    "ViewSlot",
    "AssetPrefs",
    "SlotAssumptions",
    "SlotState",
    # clarify payload
    "ClarifyOption",
    "ClarifyQuestion",
    "ClarifyCard",
    # builder payload
    "StrategyConstituent",
    "GoldInstrument",
    "Sleeve",
    "StrategyAlternative",
    "StrategyBuilderCard",
    # protocols
    "Weighting",
    "StrategyBuilder",
    "ClarifyEngine",
]
