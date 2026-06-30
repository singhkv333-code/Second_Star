"""View Markets — transmission DAG (cause -> effect) builder.

Represents and builds a view's cause->effect transmission map, persisted as
rows in ``view_transmission`` (one row per *edge*; nodes are implicit in the
``from_node`` / ``to_node`` text). This module ENRICHES an already-chosen view
— it is NOT a view generator. Two authoring paths:

  1. SEED from a chosen ``thematic_map`` scenario (winners / losers / confirm /
     invalidate) -> edges, so a THEME view picks up a research-grounded chain.
  2. MANUAL node/edge authoring by the curator (the beta default), validated
     and ordered into the same edge shape.

Both paths emit ``backend.schemas.ViewTransmissionInput`` so they compose with
``curation.attach_transmission`` / ``persist_transmission`` uniformly.

Reuses (real interfaces, pinned 2026-06-29):
  * ``backend.services.thematic_map`` — ``ThematicScenario`` dataclass
    (``.key/.label/.thesis/.winners/.losers/.confirm/.invalidate``), the
    module-level ``_SCENARIO_BY_KEY: dict[str, ThematicScenario]`` lookup, and
    ``winners_losers_block`` / ``basket_weights`` helpers. Scenario keys:
    ``monsoon_drought / conflict_war / inr_depreciation / crude_spike /
    rate_cut / slowdown``.
  * ``backend.schemas.ViewTransmissionInput`` (seq / from_node / to_node /
    edge_label / strength / evidence).
  * ``backend.models.{MarketView, ViewTransmission}``.

Nothing here imports numpy / option-chain machinery, so the module stays cheap
to import regardless of ``config.view_markets_enabled``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

from backend.schemas import ViewTransmissionInput

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import ViewTransmission


@dataclass(frozen=True)
class TransmissionDAG:
    """In-memory view of a transmission map, derived from its edges.

    ``roots`` are nodes with no incoming edge (the causes), ``leaves`` have no
    outgoing edge (the terminal effects / tradable instruments). ``is_acyclic``
    is the cycle check the review gate relies on (a transmission *map* must be
    a DAG)."""

    nodes: tuple[str, ...]
    edges: tuple[ViewTransmissionInput, ...]
    roots: tuple[str, ...]
    leaves: tuple[str, ...]
    is_acyclic: bool


def seed_transmission_from_scenario(
    scenario_key: str,
    *,
    include_losers: bool = True,
    base_seq: int = 0,
) -> list[ViewTransmissionInput]:
    """Seed a transmission chain from one ``thematic_map`` scenario.

    Builds edges ``scenario.label --(why)--> {winner ticker}`` for every winner
    (``edge_label`` = the per-name causal "why" string, ``evidence`` = the
    scenario ``thesis`` / ``confirm`` text). When ``include_losers`` is True,
    also emits ``scenario.label --(avoid: why)--> {loser ticker}`` edges (the
    AVOID leg — shorting is not wired, so they are named, not shorted). ``seq``
    is assigned sequentially starting at ``base_seq`` so the rows render in a
    stable order.

    ``scenario_key`` resolves via ``thematic_map._SCENARIO_BY_KEY``; an unknown
    key returns ``[]`` (caller surfaces "unknown scenario", never a fabricated
    chain).

    Returns ready-to-attach ``ViewTransmissionInput`` rows; the curator may
    refine them before publish.
    """
    # Import lazily so the module stays cheap to import under the feature flag
    # and to avoid a hard import-time coupling to the services layer.
    from backend.services.thematic_map import _SCENARIO_BY_KEY

    scenario = _SCENARIO_BY_KEY.get(scenario_key)
    if scenario is None:
        # Unknown key -> empty chain; caller surfaces "unknown scenario" rather
        # than a fabricated DAG.
        return []

    cause = scenario.label
    winner_evidence = scenario.thesis
    if scenario.confirm:
        winner_evidence = f"{scenario.thesis} Confirm: {scenario.confirm}"
    loser_evidence = scenario.thesis
    if scenario.invalidate:
        loser_evidence = f"{scenario.thesis} Invalidate: {scenario.invalidate}"

    edges: list[ViewTransmissionInput] = []
    seq = base_seq
    for ticker, why in scenario.winners:
        edges.append(
            ViewTransmissionInput(
                seq=seq,
                from_node=cause,
                to_node=ticker,
                edge_label=why,
                strength=None,  # never fabricate a relationship weight
                evidence=winner_evidence,
            )
        )
        seq += 1

    if include_losers:
        for ticker, why in scenario.losers:
            edges.append(
                ViewTransmissionInput(
                    seq=seq,
                    from_node=cause,
                    to_node=ticker,
                    edge_label=f"avoid: {why}",
                    strength=None,
                    evidence=loser_evidence,
                )
            )
            seq += 1

    return edges


def author_transmission_edges(
    edges: Sequence[ViewTransmissionInput | dict],
    *,
    nodes: Optional[Sequence[str]] = None,
    base_seq: int = 0,
) -> list[ViewTransmissionInput]:
    """Normalise a curator's manual node/edge authoring into ordered edges.

    Accepts raw ``ViewTransmissionInput`` or plain dicts. When ``nodes`` is
    supplied, every edge endpoint MUST be a declared node (else ``ValueError``),
    giving the manual path the same "no dangling node" guarantee the seed path
    has. Re-assigns ``seq`` sequentially from ``base_seq`` (ignoring any caller
    seq) so manual + seeded edges concatenate without collisions.

    Returns validated ``ViewTransmissionInput`` rows.
    """
    declared: Optional[set[str]] = set(nodes) if nodes is not None else None

    out: list[ViewTransmissionInput] = []
    for i, raw in enumerate(edges):
        if isinstance(raw, ViewTransmissionInput):
            edge = raw
        elif isinstance(raw, dict):
            # Pydantic v2 validation enforces the schema (non-empty endpoints,
            # 0..1 strength, …).
            edge = ViewTransmissionInput.model_validate(raw)
        else:  # pragma: no cover - defensive
            raise TypeError(
                "author_transmission_edges: edges must be ViewTransmissionInput "
                f"or dict, got {type(raw).__name__}"
            )

        if declared is not None:
            for endpoint in (edge.from_node, edge.to_node):
                if endpoint not in declared:
                    raise ValueError(
                        f"author_transmission_edges: edge endpoint {endpoint!r} "
                        "is not a declared node"
                    )

        # Re-sequence deterministically, ignoring any caller-supplied seq.
        out.append(edge.model_copy(update={"seq": base_seq + i}))

    return out


def build_dag(
    edges: Sequence[ViewTransmissionInput | "ViewTransmission"],
) -> TransmissionDAG:
    """Fold a flat edge list into a :class:`TransmissionDAG`.

    Collects the node set from the edge endpoints, computes roots/leaves, and
    runs a cycle check. Pure / no DB. Used by the review gate (a published
    transmission map must be acyclic) and by FE-render assembly.
    """
    # Normalise heterogeneous inputs (Pydantic input rows OR ORM rows) to a
    # uniform ViewTransmissionInput, ordered by seq for stable rendering.
    norm: list[ViewTransmissionInput] = []
    for e in edges:
        if isinstance(e, ViewTransmissionInput):
            norm.append(e)
        else:  # ORM ViewTransmission (duck-typed: shares the same fields)
            norm.append(
                ViewTransmissionInput(
                    seq=getattr(e, "seq", 0) or 0,
                    from_node=e.from_node,
                    to_node=e.to_node,
                    edge_label=getattr(e, "edge_label", None),
                    strength=getattr(e, "strength", None),
                    evidence=getattr(e, "evidence", None),
                )
            )
    norm.sort(key=lambda x: x.seq)

    # Node set, preserving first-seen order across the sorted edges.
    nodes: list[str] = []
    seen: set[str] = set()
    for e in norm:
        for n in (e.from_node, e.to_node):
            if n not in seen:
                seen.add(n)
                nodes.append(n)

    has_incoming: set[str] = {e.to_node for e in norm}
    has_outgoing: set[str] = {e.from_node for e in norm}
    roots = tuple(n for n in nodes if n not in has_incoming)
    leaves = tuple(n for n in nodes if n not in has_outgoing)

    is_acyclic = _is_acyclic(nodes, norm)

    return TransmissionDAG(
        nodes=tuple(nodes),
        edges=tuple(norm),
        roots=roots,
        leaves=leaves,
        is_acyclic=is_acyclic,
    )


def _is_acyclic(
    nodes: Sequence[str], edges: Sequence[ViewTransmissionInput]
) -> bool:
    """DFS three-colour cycle check (self-loops count as a cycle)."""
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for e in edges:
        adj.setdefault(e.from_node, []).append(e.to_node)
        adj.setdefault(e.to_node, [])

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n: WHITE for n in adj}

    def visit(node: str) -> bool:
        colour[node] = GREY
        for nxt in adj[node]:
            if colour[nxt] == GREY:
                return False  # back-edge -> cycle
            if colour[nxt] == WHITE and not visit(nxt):
                return False
        colour[node] = BLACK
        return True

    return all(visit(n) for n in adj if colour[n] == WHITE)


def persist_transmission(
    db: "Session",
    view_id: str,
    edges: Sequence[ViewTransmissionInput],
    *,
    replace: bool = True,
) -> list["ViewTransmission"]:
    """Write transmission edges for a view to ``view_transmission``.

    When ``replace`` is True (the default), existing rows for ``view_id`` are
    deleted first so re-running authoring is idempotent. Inserts one
    ``ViewTransmission`` per edge (HARD FK ``view_id`` -> ``market_views.id``,
    ON DELETE CASCADE per the model). Does NOT commit — the caller owns the
    transaction (matches the curation-service unit-of-work). Returns the
    persisted ORM rows ordered by ``seq``.

    Reads/writes: ``view_transmission`` (write); ``market_views`` (FK target).
    """
    from backend.models import ViewTransmission

    if replace:
        (
            db.query(ViewTransmission)
            .filter(ViewTransmission.view_id == view_id)
            .delete(synchronize_session=False)
        )

    rows: list[ViewTransmission] = []
    for edge in edges:
        row = ViewTransmission(
            view_id=view_id,
            seq=edge.seq,
            from_node=edge.from_node,
            to_node=edge.to_node,
            edge_label=edge.edge_label,
            strength=edge.strength,
            evidence=edge.evidence,
        )
        db.add(row)
        rows.append(row)

    # Flush (not commit) so PKs/defaults populate while the caller keeps the
    # transaction open (curation-service unit-of-work).
    db.flush()
    rows.sort(key=lambda r: r.seq)
    return rows


def load_transmission(db: "Session", view_id: str) -> list["ViewTransmission"]:
    """Read a view's transmission edges ordered by ``seq`` (read-only)."""
    from backend.models import ViewTransmission

    return (
        db.query(ViewTransmission)
        .filter(ViewTransmission.view_id == view_id)
        .order_by(ViewTransmission.seq)
        .all()
    )


__all__ = [
    "TransmissionDAG",
    "seed_transmission_from_scenario",
    "author_transmission_edges",
    "build_dag",
    "persist_transmission",
    "load_transmission",
]
