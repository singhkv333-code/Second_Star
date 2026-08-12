# Charto alerts — full feature test plan

The fast regression suite is `pytest -q charto/data/test_alerts.py`. It checks
the expression state machine without touching the market or user databases.

## 1. Expression matrix

Run every operator against below / equal / above transitions:

- `cross`, `cross_up`, `cross_down`, `above`, `below`.
- `enters`, `exits`, including reversed band endpoints.
- `rises_pct`, `falls_pct`, `changes_pct` over 1 and N bars.
- `is_true` for a candle, chart pattern, divergence and results event.
- AND and OR rules containing two to four mixed event/state conditions.

Resolve every operand family at current and historical bars:

- OHLCV and derived prices; day, previous-day and trailing-window fields.
- Every registered indicator, including multi-line indicators and `[n]`.
- Averages, 20-session POC/VAH/VAL, drawing references and detectors.
- Refuse missing history, missing drawings, zero-volume profiles, unknown
  indicators/lines, impossible magnitudes and non-finite numeric controls.

## 2. Frequency and lifecycle matrix

For `once`, `per_bar`, `per_bar_close` and `per_day`, prove:

- No fire on creation when a condition is already true.
- No duplicate in the same bucket; re-fire only after reset in a later bucket.
- Five-minute close rules do not fire on minute 1–4 of the forming bar.
- Pause prevents evaluation; resume and one-shot re-arm seed current state.
- Edit preserves state and expiry unless the user explicitly changes them.
- Expiry pauses the rule with a visible reason; an expired rule cannot re-arm
  until the expiry is extended or removed.
- Delete removes the rule from storage and the in-memory symbol index.

## 3. Persistence and recovery

Use a temporary SQLite account database and synthetic bars:

1. Arm below a level and persist `cstate` plus `last_eval_ts`.
2. Stop the engine, write bars that cross, and restart.
3. Verify exactly one log row with the bar timestamp, observed value, resolved
   target and `late=1`.
4. Restart again and verify it does not duplicate the recovered fire.
5. Corrupt one rule address and verify it pauses while other rules on the same
   symbol continue evaluating.

Also run `PRAGMA integrity_check` and `PRAGMA foreign_key_check`, and verify
that bars remain in `charto_bars.db` while rules/logs remain in
`charto_users.db` across deployment and restart.

## 4. Feed and delivery failure matrix

Exercise healthy, stale, disconnected, unsubscribed and daily-only feeds:

- Disconnect before, during and after a crossing.
- Expire the Kite token during market hours, then reconnect with a new token.
- Kill the dataserver with a forming bar and restart it.
- Fill the worker queue and disconnect a slow SSE subscriber.
- Drop the browser SSE connection while the engine fires, reconnect, and prove
  the durable log reconciles the missed event exactly once.
- Switch accounts while SSE is open and prove no event crosses user boundaries.
- Close every browser tab: the server must still fire and persist, while the UI
  must not claim push delivery that is not configured.

Production acceptance should require a supervised venue process with a health
alarm on connection state, last-message age, worker queue drops and catch-up
failures. A reconnect must gap-fill before declaring the feed healthy.

## 5. UI and accessibility

Automate with Playwright at desktop and 390px mobile widths:

- Entry from the alerts panel, watchlist bell, chart-axis plus and drawing menu.
- Create/edit all condition shapes; add/remove four conditions; AND/OR switch.
- Preview race: a slow old response must never overwrite a newer rule.
- Create stays disabled until the latest preview resolves successfully.
- Fired alerts say Re-arm; paused alerts say Resume; failures remain visible.
- Keyboard-only creation, focus return, Escape behavior, labels, accessible
  names, contrast, touch controls and no clipped menus/dialog fields.

## 6. Load and security

- 200 alerts per user across several symbols; measure evaluation latency,
  SQLite lock time, queue depth and tick-loop impact.
- Concurrent create/edit/delete/fire/list operations under WAL.
- Authentication required on list, stream, check and every mutation.
- User A cannot read, edit, delete or subscribe to user B's alerts or logs.
- Fuzz JSON sizes, malformed addresses, NaN/infinity, extreme windows and notes;
  every refusal must be a bounded 4xx response, never a server exception.

