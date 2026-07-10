# Branching & deploy model

```
main            production — protected, every merge auto-deploys (see .github/workflows/deploy-*.yml)
 └─ develop     integration branch — protected, PRs merge here first
     ├─ backend         general backend work (pivot/backend/** outside chat)
     ├─ chat-services   chat pipeline work (pivot/backend/services/chat_service.py,
     │                  tool_router.py, tool_executor.py, prompts/*)
     └─ frontend        pivot-next/**
```

## Day to day

1. Work directly on the component branch that matches what you're touching
   (`backend`, `chat-services`, `frontend`) — push freely, no PR needed there.
2. When ready to integrate, open a PR from your component branch into
   `develop`. `PR validation` (lint + typecheck + import-sanity) must pass.
   Resolve conflicts in the PR, not by force-pushing over someone else's work.
3. Periodically (or per release), open a PR from `develop` into `main`. Merging
   to `main` triggers the real Azure deploy — backend and frontend each
   redeploy only if their own path changed (see the path filters in
   `deploy-backend.yml` / `deploy-frontend.yml`).
4. Keep component branches current with `develop` (`git merge develop` into
   your component branch periodically) so integration PRs stay small.

## Why three component branches instead of one

`backend`, `chat-services`, and `frontend` touch mostly-disjoint files. Working
on separate branches means two people (or two agent sessions) editing, say,
the chat prompt-routing logic and the Next.js UI at the same time don't
collide on the same working tree or force a rebase against unrelated changes.
`chat-services` is split out from `backend` specifically because the chat
pipeline (`chat_service.py`, `tool_router.py`, `tool_executor.py`,
`prompts/`) is the highest-churn, most contended area of the codebase.

## Branch protection (both `main` and `develop`)

- No direct pushes — changes land via PR only
- `PR validation` status check required before merging
- No force-push, no branch deletion
