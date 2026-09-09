# View Markets — archived production data, 2026-09-05

The opinion-markets ("View Markets") direction was retired on 2026-09-05. Its
seven tables were dropped from `pivot_db` by migration `0027_drop_view_markets`.
These files are the full contents at the moment of the drop, exported straight
from Azure — 107 rows.

| File | Rows | Note |
|---|---:|---|
| `market_views.json` | 25 | curated views |
| `view_expressions.json` | 60 | tiered expressions per view |
| `view_transmission.json` | 12 | cause→effect chains |
| `view_confidence.json` | 6 | two-dial scores |
| `view_positions.json` | 4 | **two rows belong to `user_id` 2, a real person** — entered 22 Jul 2026, status `open`. The other two are eval users 678/686. |
| `view_follows.json` | 0 | |
| `view_expectations.json` | 0 | |

`view_positions` rows carry equity `legs` with entry prices, so they are
readable as ordinary basket positions if anyone ever wants to reconstruct them
in the paper book. Nothing else here has a successor and nothing should be
restored: the direction is a named non-negotiable in `CLAUDE.md` section 4.

Kept as JSON rather than a `pg_dump` so it stays readable without a server to
restore into.
