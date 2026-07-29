"""Seed the curated View Packs (viewpack01/02) into the View Markets DB and
wire them for real deploy — the hybrid path for the Opinions tab.

Background
----------
The Opinions/Views tab renders 100% from static curated JSON
(``pivot-next/components/views/pack/viewpack0{1,2}.details.json``). Those
expressions carried ``is_deployable: false`` and slug ids
(``elon_crypto-balanced``) that don't exist in Postgres, so the "Deploy this
strategy" button was permanently dead — no amount of backend deploy wiring
changed it, because the tab never queried the backend.

This script closes the gap WITHOUT regressing the rich static display:

  1. For every pack view + expression, mint a DETERMINISTIC uuid5 from its slug
     (idempotent: re-running maps to the same rows) and upsert a MarketView +
     ViewExpression row carrying the pack's config (structure/scores/label/…),
     so ``POST /api/views/expressions/{uuid}/deploy`` resolves a real row.
  2. Dry-run ``deploy_expression`` on each seeded expression (rolled back) to
     learn — honestly — which ones actually arm. Curated option/hedge/pair
     tiers are stubs (no template/underlying) and stay NON-deployable.
  3. Patch the pack JSON IN PLACE: each expression's ``id`` becomes its DB uuid
     and ``is_deployable`` becomes the dry-run result. The tab keeps rendering
     the static pack for display; the button now targets a live DB row.

Register-not-execute is preserved end-to-end: deploy only ARMS a draft
workflow; no order is placed here, no workflow is activated, no My Views
position is written (that happens when the user presses Deploy in the app).

Run:
  cd /Users/karanveersingh/Downloads/Second_Star/pivot
  .venv/bin/python scripts/seed_viewpacks_to_db.py            # seed + patch
  .venv/bin/python scripts/seed_viewpacks_to_db.py --dry-run  # report only
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from typing import Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.database import SessionLocal  # noqa: E402
from backend.models import (  # noqa: E402
    ExpressionKind,
    ExpressionTier,
    MarketView,
    ViewExpression,
    ViewStatus,
    ViewType,
)
from backend.view_markets.deployment.deploy import deploy_expression  # noqa: E402

# Stable namespace so slug -> uuid is reproducible across runs / machines.
_NS = uuid.UUID("6f0b6d2e-2a4c-5b7e-9c1d-000000000000")

# The user who owns the dry-run draft (never persisted — rolled back). Only
# used so deploy_expression's user_id gate passes during the probe.
_PROBE_USER_ID = 1

_PACK_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "pivot-next", "components", "views", "pack",
)
_PACKS = ["viewpack01.details.json", "viewpack02.details.json"]


def _view_uuid(slug: str) -> str:
    return str(uuid.uuid5(_NS, f"view:{slug}"))


def _expr_uuid(slug: str) -> str:
    return str(uuid.uuid5(_NS, f"expr:{slug}"))


def _parse_dt(val: Any) -> Optional[datetime]:
    if not isinstance(val, str) or not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        return None


def _expr_config(e: dict) -> dict:
    """Reconstruct the ViewExpression.config JSON from the flattened pack
    expression (the pack IS the API's ExpressionDetail shape — structure/scores/
    label/… are top-level there; deploy + detail read them from ``config``)."""
    # Basket expressions authored FE-first carry their book only as display
    # ``holdings`` (name/symbol/weight_pct) with an EMPTY ``structure`` — the
    # deploy machinery then finds "no tradeable equity legs" and the basket
    # seeds as non-deployable (the renewable/gold packs). Derive the deploy
    # contract (structure.weights fractions + members_long) from holdings so
    # the seeded row arms exactly the basket the page displays.
    structure = dict(e.get("structure") or {})
    if not structure.get("weights") and not structure.get("members_long"):
        holds = [
            h for h in (e.get("holdings") or [])
            if isinstance(h, dict) and h.get("symbol")
            and isinstance(h.get("weight_pct"), (int, float))
            and h.get("weight_pct") > 0 and h.get("position") != "short"
        ]
        total = sum(float(h["weight_pct"]) for h in holds)
        if holds and total > 0:
            structure["weights"] = {
                str(h["symbol"]): round(float(h["weight_pct"]) / total, 6)
                for h in holds
            }
            structure["members_long"] = [str(h["symbol"]) for h in holds]
            structure.setdefault("scheme", "custom_weight")
    cfg: dict[str, Any] = {
        "label": e.get("label"),
        "structure": structure,
        "instruments": e.get("instruments") or [],
        "warnings": e.get("warnings") or [],
        "disclaimer": e.get("disclaimer"),
        "expression_kind": e.get("expression_kind"),
        "tier": e.get("tier"),
        "schema_version": e.get("schema_version"),
    }
    if e.get("scores") is not None:
        cfg["scores"] = e["scores"]
    if isinstance(e.get("timing"), dict):
        cfg["timing"] = e["timing"]
    return cfg


def _upsert_view(db, slug: str, v: dict) -> str:
    vid = _view_uuid(slug)
    row = db.get(MarketView, vid)
    if row is None:
        row = MarketView(id=vid)
        db.add(row)
    row.view_type = ViewType(v.get("view_type") or "event")
    row.title = v.get("title") or slug
    row.thesis = v.get("thesis")
    row.category = v.get("category")
    row.time_horizon = v.get("time_horizon")
    row.status = ViewStatus(v.get("status") or "open")
    row.resolution_date = _parse_dt(v.get("resolution_date"))
    row.published_at = _parse_dt(v.get("published_at")) or datetime.utcnow()
    db.flush()
    return vid


def _expr_slug(e: dict) -> str:
    """The STABLE identity for uuid5 minting. First run: the pack's authored
    slug id (``renewable-growth``). The JSON patch rewrites ``id`` to the
    minted uuid, so subsequent runs must key off the preserved ``seed_slug`` —
    minting from the current (uuid) id would derive uuid5(uuid) ≠ uuid, drift
    every expression's id each run, and orphan the previously seeded rows."""
    return str(e.get("seed_slug") or e["id"])


def _upsert_expression(db, view_id: str, e: dict) -> str:
    eid = _expr_uuid(_expr_slug(e))
    row = db.get(ViewExpression, eid)
    if row is None:
        row = ViewExpression(id=eid)
        db.add(row)
    row.view_id = view_id
    row.tier = ExpressionTier(e.get("tier") or "balanced")
    row.expression_kind = ExpressionKind(e["expression_kind"])
    row.config = _expr_config(e)
    row.rationale = e.get("rationale")
    row.risk_profile = e.get("risk_profile")
    row.capital_intensity = e.get("capital_intensity")
    row.historical_strength = e.get("historical_strength")
    row.time_horizon = e.get("time_horizon")
    db.flush()
    return eid


def _probe_deployable(db, eid: str) -> bool:
    """True iff deploy_expression arms this expression. Rolled back — the draft
    workflow, its steps and the workflow_id link never persist."""
    expr = db.get(ViewExpression, eid)
    if expr is None:
        return False
    sp = db.begin_nested()
    try:
        deploy_expression(db, expr, activate=False, user_id=_PROBE_USER_ID)
        return True
    except Exception:  # noqa: BLE001 — any failure = honestly non-deployable
        return False
    finally:
        sp.rollback()


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    db = SessionLocal()
    seeded_views = seeded_exprs = deployable = 0
    # slug -> (uuid, is_deployable) so we can patch the JSON after commit.
    patch: dict[str, tuple[str, bool]] = {}
    try:
        loaded = {p: json.load(open(os.path.join(_PACK_DIR, p))) for p in _PACKS}
        for pack, details in loaded.items():
            for slug, v in details.items():
                view_id = _upsert_view(db, slug, v)
                seeded_views += 1
                for e in v.get("expressions") or []:
                    eid = _upsert_expression(db, view_id, e)
                    seeded_exprs += 1
                    patch[_expr_slug(e)] = (eid, False)
        db.flush()
        # Probe every seeded expression for real deployability.
        for slug, (eid, _) in list(patch.items()):
            ok = _probe_deployable(db, eid)
            patch[slug] = (eid, ok)
            deployable += int(ok)

        if dry_run:
            db.rollback()
            print("DRY RUN — no DB writes, no JSON patch.")
        else:
            db.commit()
            # Patch the pack JSON in place (id -> uuid, is_deployable -> probe).
            for pack, details in loaded.items():
                for slug, v in details.items():
                    for e in v.get("expressions") or []:
                        slug = _expr_slug(e)
                        eid, ok = patch[slug]
                        e["seed_slug"] = slug   # preserve the stable identity
                        e["id"] = eid
                        e["is_deployable"] = ok
                out = os.path.join(_PACK_DIR, pack)
                # ensure_ascii=True + indent=1 to match the generator's
                # encoding so the diff is JUST the id + is_deployable flips,
                # not a wholesale unicode re-escape of every em-dash line.
                with open(out, "w") as fh:
                    json.dump(details, fh, ensure_ascii=True, indent=1)
                    fh.write("\n")
            print("Patched pack JSON (expression ids + is_deployable).")

        print(
            f"views={seeded_views} expressions={seeded_exprs} "
            f"deployable={deployable}/{seeded_exprs}"
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
