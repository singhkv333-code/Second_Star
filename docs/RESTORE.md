# Restore points — the platform-merge work, 2026-09-05

Every phase of the Pivot/Charto merge is a separate commit with a named tag, so
any one of them can be undone without unwinding the rest. **Nothing has been
pushed to any remote, and nothing has been applied to the VM.**

## The tags

| Tag | Commit | State |
|---|---|---|
| `restore/before-merge` | `ff06fdfb` | Untouched. The last commit before any of this work. |
| `restore/phase0-truth` | `40c8eeba` | CLAUDE.md/AGENTS.md rewritten, `docs/DATA_MAP.md` added, four false statements corrected, two dead files removed. |
| `restore/phase1-excision` | `35db45ec` | Opinion markets gone from code, schema models, UI and prose. |
| `restore/phase2-api` | `02fc6de0` | `pivot-api.service`, the `/api/pivot/` nginx block, and the Charto-session seam. Built, **not installed**. |
| `wip/pre-phase2-2026-09-05` | branch | The 56 pre-existing uncommitted files (execution mode, stock research panels, chat/backtest work), snapshotted verbatim. Not merged into anything. |

## How to undo

**Undo everything, keep the work recoverable:**

    git reset --hard restore/before-merge

The commits stay in the reflog and under their tags; nothing is lost.

**Undo one phase only** (they are independent — Phase 2 does not depend on
Phase 1's deletions, and Phase 1 does not depend on Phase 0's prose):

    git revert 02fc6de0      # drop the pivot-api unit + session seam
    git revert 35db45ec      # bring the opinion-markets branch back
    git revert 40c8eeba      # restore the old CLAUDE.md

**Recover one file from the WIP snapshot:**

    git checkout wip/pre-phase2-2026-09-05 -- pivot/backend/execution/
    git checkout wip/pre-phase2-2026-09-05 -- pivot-next/components/stock/ResearchPanel.tsx

**Recover the opinion-markets data** (107 rows, exported before anything was
dropped, including two `view_positions` rows owned by a real user):

    pivot/archive/view_markets_2026_09_05/*.json

## What is NOT yet reversible-by-git, because it has not happened

These are the deliberate, separate acts still outstanding. None has run:

1. **Migration `0027_drop_view_markets`** — written, `alembic heads` sees it,
   **not applied**. The seven tables still exist in `pivot_db` with their rows.
   The code no longer references them, so they sit orphaned, which is harmless.
   It is irreversible by design once run; the JSON archive is the backup.
2. **`provision_pivot_api.sh`** — nothing is installed on the VM. No unit, no
   sudoers file, no nginx change. Production is exactly as it was.
3. **The nginx `/api/pivot/` block** — in the repo, not on the box.
   `apply_nginx.sh` installs it with a syntax check, a backup and a rollback.

## Rolling back a VM change, if one is ever applied

    # nginx — apply_nginx.sh keeps a timestamped backup and self-rolls-back on
    # a failed probe, but by hand:
    sudo cp /etc/nginx/sites-available/charto.conf.bak.<ts> /etc/nginx/sites-available/charto.conf
    sudo nginx -t && sudo systemctl reload nginx

    # the API unit
    sudo systemctl disable --now pivot-api.service
    sudo rm -f /etc/systemd/system/pivot-api.service /etc/sudoers.d/pivot-api
    sudo systemctl daemon-reload

Neither touches Charto: `charto.service`, `charto-web.service` and
`charto-research.service` are independent units and the `/api/pivot/` route is
additive.
