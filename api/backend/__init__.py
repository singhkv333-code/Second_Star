"""Pivot's API package.

Importing it first accepts the retired CHARTO_* environment names as their
PIVOT_* successors (see dataserver/envcompat.py for why a missed rename would
fail silently). An explicitly set PIVOT_* name always wins.
"""
import os as _os

_RENAMED = {
    "CHARTO_BACKEND": "PIVOT_DATASERVER_ORIGIN",
    "CHARTO_EXECUTION": "PIVOT_CHART_EXECUTION",
    "CHARTO_UPSTREAM": "PIVOT_DATASERVER_UPSTREAM",
    "CHARTO_PREVIEW_DIR": "PIVOT_CHART_DIR",
}
for _old, _value in list(_os.environ.items()):
    if _old.startswith("CHARTO_"):
        _os.environ.setdefault(_RENAMED.get(_old, "PIVOT_" + _old[len("CHARTO_"):]), _value)
