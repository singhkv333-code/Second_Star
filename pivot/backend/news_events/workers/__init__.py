"""Background workers for news_events.

Each worker registers one or more APScheduler jobs against the
existing AsyncIOScheduler. They run on the same event loop as the
rest of the backend — no separate process required.

Phase 1 ships ``poller`` only. The funnel worker (Phase 2+) drains a
per-event queue through Stages 1-8 and lives alongside as
``funnel.py``; adaptive intervals (election nights, MPC days) become
``adaptive.py`` in Phase 6.
"""
