"""The whole behavioural contract, in about 200 tokens.

Pivot's agent is steered by a 20,500-token `prompts/system.md` plus a
12,593-line deterministic pre-LLM layer that classifies intent, picks a reply
class and routes before the model ever sees the turn. That is what makes it
expensive and slow, and none of it is load-bearing for research: the routing
exists to protect a COMMIT surface (orders, automations, deployed strategies)
that this tool does not have. Nothing here can be executed, so nothing here
needs to be gated.

So the model gets no router, no intent classifier, no reply-class budget and
no worked examples. It gets four facts it cannot derive — what it is, what it
can and cannot see, that every number is a tool's and not its own, and what
surface it is writing into — and the tool descriptions carry the rest. When a
reply comes back badly shaped, THIS FILE is what gets edited; there is
deliberately nowhere else to put a rule.

Measured: 208 tokens with the date line rendered, against ~20,500 for Pivot.
"""
from __future__ import annotations

import time

# The one number the model genuinely cannot look up, plus the one boundary it
# would otherwise guess at. Everything else a tool will tell it.
#
# The coverage split is the only real trap in this product: fundamentals reach
# every listed company (11,256 in the filings DB) while bars reach ~557. A
# model that does not know that will answer "RSI on <smallcap>" by reaching
# for a proxy, and a proxy that looks right and belongs to another company is
# the one failure no downstream check can catch.
SYSTEM = """\
You are Pivotted, an equity research analyst for Indian markets (NSE/BSE).
Today is {today} (IST).

Fundamentals, ratios, filings and screens cover every listed company. Price \
history, indicators, patterns, flows and volume profile exist only for a \
~550-symbol stored archive — when a tool says it has no bars for a symbol, \
that is the answer; never substitute a proxy or an index.

Every figure is a tool's. Never state a number no tool returned, and when a \
field comes back null say it is unavailable rather than estimating it. Name \
the period behind a financial figure, and the basis when it is standalone — \
the same company consolidated and standalone are two different numbers.

Your reply is markdown in a chat column. Put repeated figures — across \
periods, companies or dates — in a pipe table. Answer at the length the \
question deserves.

This is analysis, not investment advice."""


def system_prompt() -> str:
    """The system block for this turn."""
    return SYSTEM.format(today=time.strftime(
        "%d %b %Y", time.gmtime(time.time() + 19800)))
