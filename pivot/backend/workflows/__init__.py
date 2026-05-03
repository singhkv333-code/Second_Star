"""Pivot Agent System v1 — workflow engine, registry, and step executors.

See docs/ARCHITECTURE.md for the design and docs/API_CONTRACT.md for
the externally-visible shapes. The registry (registry.py) is the single
source of truth for the step-type catalog; both the chatbot's
propose_workflow tool and the frontend's StepConfigDrawer pull from it
through GET /api/step-types.
"""
from __future__ import annotations

# Importing the registry triggers @register_step decorator side-effects
# in every steps/* module, populating STEP_REGISTRY at import time.
from backend.workflows import registry  # noqa: F401
from backend.workflows.steps import (  # noqa: F401
    actions,
    conditions,
    control,
    fetches,
    notify,
    triggers,
)
