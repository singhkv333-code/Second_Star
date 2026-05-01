"""Agent System (Workflows v1) router.

Day 1 scope: only GET /api/step-types is wired up — frontend needs the
catalog to render the step picker and config drawers.

CRUD endpoints (POST/GET/PATCH /api/workflows, activate/pause/archive,
manual run) land Day 2; runs/approvals/webhooks/run-stream land in
their sibling routers (runs.py, approvals.py, webhooks.py,
run_stream.py).

Per docs/API_CONTRACT.md §1, every workflow endpoint is JWT-bearer
authenticated and user-scoped. The catalog endpoint is the one
exception — it returns the same data for every authenticated user, so
we still require auth (no public introspection of supported step
types) but don't filter by user.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from backend.auth.jwt_handler import get_user_id_from_token
from backend.schemas import StepTypeCatalogResponse
from backend.workflows.registry import get_catalog


router = APIRouter(prefix="/api", tags=["Agents"])


def _require_user(authorization: str = Header(default=None)) -> int:
    """Auth dependency mirroring the existing routers (sip.py,
    portfolio.py, etc.). Returns the JWT-decoded user_id or raises 401.

    We re-implement here rather than reaching into another router so
    every workflow endpoint has a single, auditable auth path.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.replace("Bearer ", "", 1)
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


@router.get(
    "/step-types",
    response_model=StepTypeCatalogResponse,
    summary="Step-type catalog",
    description=(
        "Returns the full catalog of supported workflow step types — their "
        "JSON Schema (draft 2020-12) for config validation, output schemas, "
        "and UI metadata (icon, category, label). The frontend caches this "
        "for 5 minutes; bumping `catalog_version` invalidates the cache. "
        "See docs/API_CONTRACT.md §8.1."
    ),
)
def get_step_types(
    # Authentication is required (per API_CONTRACT.md §1) but the
    # response is identical for every user — there is no per-user
    # filtering. The dependency only enforces the bearer-token check.
    _user_id: int = Depends(_require_user),
) -> StepTypeCatalogResponse:
    catalog = get_catalog()
    # model_validate() coerces the raw dict into the typed response
    # model so FastAPI's OpenAPI generator has the full schema.
    return StepTypeCatalogResponse.model_validate(catalog)
