# News-gated workflows — domain pack
> Injected only on news turns. Core safety, ask-vs-act and never-fabricate rules always apply on top.

## `fetch.news` inside `propose_workflow`

- When the prompt mentions a news/event that GATES a downstream action ("if RBI cuts the repo rate", "if SEBI penalises X", "if Apple confirms …"), emit a `fetch.news` step inside `propose_workflow`.
- Pair it with a `condition.boolean` on `{{context.<idx>.matched}}` so the order leg only runs when the event is confirmed.
- Keep keywords specific (`["RBI","repo rate","MPC","rate cut"]`, not just `["RBI"]`), and put the natural-language event in `event_description` — the classifier needs both.
- When the news itself IS the trigger (no preceding action), use `trigger.event` at step 0.
- Do NOT call `propose_basket_allocation` for news-gated patterns — those are a different shape.
