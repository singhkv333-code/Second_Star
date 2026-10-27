"""Accept the retired CHARTO_* environment names as their PIVOT_* successors.

Env files written before the rename (the VM's /etc/pivot/*.env, a laptop's
api/.env) still say CHARTO_USERS_DB and friends. A missed rename would not
fail loudly: an unset PIVOT_USERS_DB falls back to a default path and SQLite
creates an empty database there. So the old name is copied to the new one at
start-up, and an explicitly set new name always wins.

Import it before anything that reads the environment. It can go once no env
file carries a CHARTO_ key.
"""
import os

RENAMED = {
    "CHARTO_BACKEND": "PIVOT_DATASERVER_ORIGIN",
    "CHARTO_EXECUTION": "PIVOT_CHART_EXECUTION",
    "CHARTO_UPSTREAM": "PIVOT_DATASERVER_UPSTREAM",
    "CHARTO_PREVIEW_DIR": "PIVOT_CHART_DIR",
}

for _old, _value in list(os.environ.items()):
    if _old.startswith("CHARTO_"):
        os.environ.setdefault(RENAMED.get(_old, "PIVOT_" + _old[len("CHARTO_"):]), _value)


def legacy_name(name: str) -> str | None:
    """The retired spelling of a PIVOT_* name, for readers of raw .env lines."""
    for old, new in RENAMED.items():
        if new == name:
            return old
    return "CHARTO_" + name[len("PIVOT_"):] if name.startswith("PIVOT_") else None
