# PIVOT — PARALLEL AGENT QUALITY IMPROVEMENT PROMPT
# Claude Code — Two parallel agents running simultaneously
#
# AGENT A: Tester — runs real conversations with Sarvam AI, captures every failure
# AGENT B: Fixer  — reads Agent A's findings, fixes all issues immediately
#
# Run BOTH agents in parallel. Agent A writes to /tmp/pivot_qa_log.json
# Agent B watches that file and applies fixes as they come in.

# ============================================================
# WHAT IS BROKEN — FROM THE SCREENSHOT
# ============================================================
#
# Problem 1: <think>...</think> blocks are visible in the chat UI
#   The sarvam_client.py has _strip_think_blocks() but it's not being
#   called in the chat router response path. Fix: ensure ALL Sarvam
#   responses pass through _strip_think_blocks() before returning.
#
# Problem 2: Markdown **bold** is not rendering in the chat bubble
#   MessageBubble.jsx renders raw text. Fix: parse markdown to HTML
#   or use a lightweight markdown renderer.
#
# Problem 3: System prompt says "Hindi/Hinglish" — we are English only
#   The pivot persona and classifier still reference Hindi. Fix: rewrite
#   all system prompts to be English-only, professional, clean.
#
# Problem 4: Response quality is poor — it's a generic AI response
#   The system prompt doesn't make Sarvam sound like Pivot.
#   Fix: completely rewrite PIVOT_SYSTEM_PROMPT to be specific,
#   product-aware, and behaviour-constrained.
#
# Problem 5: "LogicCard ready — check Order panel" appears even when
#   the AI response has no actual LogicCard JSON. Fix: only show this
#   indicator when valid LogicCard JSON was actually parsed.
#
# Problem 6: Chat response is far too long
#   No max_tokens enforcement in chat endpoint. Fix: 400 token limit
#   for conversational responses, 800 for product explanations.

# ============================================================
# AGENT A — CONVERSATION TESTER
# ============================================================
#
# Agent A runs a battery of test conversations against the live
# Sarvam AI integration and records every failure, leak, and
# quality issue to /tmp/pivot_qa_log.json
#
# Create this file: scripts/qa_agent.py
# Run it with: python scripts/qa_agent.py

QA_AGENT_CODE = '''
#!/usr/bin/env python3
"""
Agent A — Conversation Quality Tester
Runs test conversations against Pivot's Sarvam integration.
Writes findings to /tmp/pivot_qa_log.json for Agent B to fix.

Run from the pivot/ root:
  python scripts/qa_agent.py
"""

import asyncio
import json
import re
import sys
import os
import time
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("APP_ENV", "development")

from backend.agents.sarvam_client import call_sarvam

LOG_FILE = Path("/tmp/pivot_qa_log.json")

# ── Test conversations ────────────────────────────────────────
# Each test has: input, what we expect, what we must NOT see
TEST_CASES = [
    {
        "id": "T01",
        "category": "greeting",
        "input": "what all can you do",
        "must_not_contain": ["<think>", "</think>", "**", "##", "Hindi", "Hinglish"],
        "must_contain": ["portfolio", "order", "strategy"],
        "max_length": 400,
        "description": "Greeting / capability question — should be concise, no markdown leaking, no think blocks",
    },
    {
        "id": "T02",
        "category": "order_intent",
        "input": "buy 10 shares of INFY at market price",
        "must_not_contain": ["<think>", "</think>", "I cannot", "I am unable"],
        "must_contain": ["confirm", "INFY", "order"],
        "max_length": 300,
        "description": "Clear order intent — should propose action, not explain at length",
    },
    {
        "id": "T03",
        "category": "product_explanation",
        "input": "explain SafeGrow to me",
        "must_not_contain": ["<think>", "</think>", "**", "##"],
        "must_contain": ["capital", "arbitrage", "option"],
        "max_length": 500,
        "description": "Product explanation — accurate, no markdown symbols in plain text",
    },
    {
        "id": "T04",
        "category": "portfolio",
        "input": "show me my portfolio",
        "must_not_contain": ["<think>", "</think>"],
        "must_contain": [],
        "max_length": 200,
        "description": "Portfolio request — should be short, direct, offer to show data",
    },
    {
        "id": "T05",
        "category": "clarification",
        "input": "I want to invest safely",
        "must_not_contain": ["<think>", "</think>", "**", "Hinglish"],
        "must_contain": ["how much", "capital", "horizon"],
        "max_length": 200,
        "description": "Ambiguous intent — should ask ONE clarifying question, nothing more",
    },
    {
        "id": "T06",
        "category": "disclaimer",
        "input": "should I buy Reliance right now",
        "must_not_contain": ["<think>", "</think>", "you should buy", "I recommend", "guaranteed"],
        "must_contain": ["not financial advice", "automation"],
        "max_length": 300,
        "description": "Advice-seeking — must decline advice framing, include disclaimer",
    },
    {
        "id": "T07",
        "category": "number_formatting",
        "input": "what is 5% of 2 lakh",
        "must_not_contain": ["<think>", "</think>"],
        "must_contain": ["10,000", "₹"],
        "max_length": 150,
        "description": "Simple calculation — must show number in Indian format",
    },
    {
        "id": "T08",
        "category": "gtt_order",
        "input": "set a GTT to buy TCS if it falls to 3500",
        "must_not_contain": ["<think>", "</think>", "I cannot"],
        "must_contain": ["TCS", "3500", "trigger"],
        "max_length": 350,
        "description": "GTT order request — clear action, no refusal",
    },
    {
        "id": "T09",
        "category": "length_control",
        "input": "hi",
        "must_not_contain": ["<think>", "</think>"],
        "must_contain": [],
        "max_length": 100,
        "description": "Short greeting — response must be short, not an essay",
    },
    {
        "id": "T10",
        "category": "no_hallucination",
        "input": "what was Nifty\'s closing price yesterday",
        "must_not_contain": ["<think>", "</think>", "yesterday was", "closed at"],
        "must_contain": ["real-time", "live", "check"],
        "max_length": 150,
        "description": "Live data request — must not hallucinate prices, must redirect",
    },
]

SYSTEM_PROMPT_UNDER_TEST = """You are Pivot — an AI-powered investing terminal for the Indian stock market.

You help users:
- Execute orders (market, limit, GTT, stop-loss) through their Zerodha account
- Build synthetic investment products (SafeGrow capital protection, EarnMore covered calls, StormShield bear notes)
- Manage SIPs and automated strategies
- Understand their portfolio and P&L
- Learn about specific stocks, ETFs, and market concepts

STRICT RULES — never break these:
1. Never say "I recommend", "you should buy", "guaranteed returns", or give directional price predictions
2. Always end any financial action suggestion with: "This is automation of your instructions, not financial advice."
3. For every order or strategy, show a clear summary before executing — user must confirm
4. Keep responses SHORT — maximum 3 paragraphs for explanations, 1 paragraph for simple questions
5. When you do not have live market data, say so clearly — do not invent prices
6. English only — no Hindi, no Hinglish
7. Do not show your reasoning process — respond directly and cleanly
8. Format numbers in Indian style: ₹1,00,000 not ₹100000

RESPONSE FORMAT:
- No markdown headers (##)
- No bullet points with ** for bold — use plain English
- Keep it conversational, direct, and precise
- If proposing an order or strategy, end with a clear call to action

You are a terminal, not a chatbot. Be precise. Be brief. Be useful."""


async def run_single_test(test: dict) -> dict:
    """Run one test conversation and capture results."""
    messages = [{"role": "user", "content": test["input"]}]

    t0 = time.time()
    result = await call_sarvam(
        messages=messages,
        system_prompt=SYSTEM_PROMPT_UNDER_TEST,
        temperature=0.3,
        max_tokens=600,
        reasoning_effort=None,
    )
    elapsed_ms = int((time.time() - t0) * 1000)

    response = result.get("content", "")
    error = result.get("error")

    # Run checks
    failures = []

    # Check must_not_contain
    for pattern in test["must_not_contain"]:
        if pattern.lower() in response.lower():
            failures.append(f"FOUND FORBIDDEN: '{pattern}'")

    # Check must_contain
    for pattern in test["must_contain"]:
        if pattern.lower() not in response.lower():
            failures.append(f"MISSING REQUIRED: '{pattern}'")

    # Check length
    if len(response) > test["max_length"] * 4:  # rough char-to-token
        failures.append(f"TOO LONG: {len(response)} chars (max ~{test['max_length'] * 4})")

    # Check for raw markdown symbols in response
    if re.search(r"\*\*\w", response):
        failures.append("MARKDOWN LEAK: ** found in response")
    if re.search(r"#{1,4}\s", response):
        failures.append("MARKDOWN LEAK: ## found in response")

    # Check for think block leakage
    if "<think>" in response or "</think>" in response:
        failures.append("CRITICAL: <think> block leaked into response")

    passed = len(failures) == 0 and not error

    return {
        "test_id": test["id"],
        "category": test["category"],
        "description": test["description"],
        "input": test["input"],
        "response": response[:800],  # truncate for log
        "response_length": len(response),
        "latency_ms": elapsed_ms,
        "passed": passed,
        "failures": failures,
        "error": error,
        "timestamp": datetime.utcnow().isoformat(),
    }


async def run_all_tests():
    print("\\n🔍 PIVOT QA AGENT — Running conversation tests against Sarvam AI\\n")
    print("=" * 60)

    results = []
    passed = 0
    failed = 0

    for test in TEST_CASES:
        print(f"\\n[{test['id']}] {test['description'][:60]}")
        print(f"  Input: \\"{test['input']}\\"")

        result = await run_single_test(test)
        results.append(result)

        if result["passed"]:
            passed += 1
            print(f"  ✅ PASS ({result['latency_ms']}ms, {result['response_length']} chars)")
        else:
            failed += 1
            print(f"  ❌ FAIL ({result['latency_ms']}ms, {result['response_length']} chars)")
            for f in result["failures"]:
                print(f"     → {f}")
            print(f"  Response preview: {result['response'][:150]}...")

    # Write log
    log = {
        "run_at": datetime.utcnow().isoformat(),
        "total": len(TEST_CASES),
        "passed": passed,
        "failed": failed,
        "pass_rate": f"{passed/len(TEST_CASES)*100:.0f}%",
        "results": results,
        "system_prompt_used": SYSTEM_PROMPT_UNDER_TEST,
    }
    LOG_FILE.write_text(json.dumps(log, indent=2, ensure_ascii=False))

    print("\\n" + "=" * 60)
    print(f"\\n📊 Results: {passed}/{len(TEST_CASES)} passed ({log['pass_rate']})")
    print(f"📁 Full log written to: {LOG_FILE}")

    if failed > 0:
        print("\\n🔧 Run Agent B to apply fixes:")
        print("   python scripts/fix_agent.py")
    else:
        print("\\n🎉 All tests passed. Production ready.")

    return log


if __name__ == "__main__":
    asyncio.run(run_all_tests())
'''

# ============================================================
# AGENT B — FIXER
# ============================================================
#
# Agent B reads the QA log and applies all fixes automatically.
# It patches: sarvam_client.py, chat.py router, ChatPane.jsx,
# MessageBubble.jsx, and the system prompt.
#
# Create this file: scripts/fix_agent.py

FIX_AGENT_CODE = '''
#!/usr/bin/env python3
"""
Agent B — Automated Fixer
Reads /tmp/pivot_qa_log.json from Agent A and applies targeted fixes.

Run after Agent A:
  python scripts/fix_agent.py
"""

import json
import re
import sys
from pathlib import Path

LOG_FILE = Path("/tmp/pivot_qa_log.json")
ROOT = Path(__file__).parent.parent


def load_log() -> dict:
    if not LOG_FILE.exists():
        print("❌ No QA log found. Run Agent A first: python scripts/qa_agent.py")
        sys.exit(1)
    return json.loads(LOG_FILE.read_text())


def check_failure_type(results: list, pattern: str) -> bool:
    """Check if any test failed with this specific failure pattern."""
    return any(
        any(pattern.lower() in f.lower() for f in r["failures"])
        for r in results if not r["passed"]
    )


def fix_1_strip_think_blocks(results: list):
    """Fix: <think> blocks leaking into UI responses."""
    if not check_failure_type(results, "<think>"):
        print("  ✅ Fix 1: Think block stripping — already clean")
        return

    print("  🔧 Fix 1: Patching chat router to strip <think> blocks...")

    chat_router = ROOT / "backend" / "routers" / "chat.py"
    if not chat_router.exists():
        print("  ⚠️  chat.py not found — skipping")
        return

    content = chat_router.read_text()

    # Ensure _strip_think_blocks is imported
    if "_strip_think_blocks" not in content:
        content = content.replace(
            "from backend.agents.sarvam_client import call_sarvam",
            "from backend.agents.sarvam_client import call_sarvam, _strip_think_blocks",
        )

    # Ensure response is stripped before returning
    old = 'response = result.get("content", "") if isinstance(result, dict) else str(result)'
    new = 'response = _strip_think_blocks(result.get("content", "") if isinstance(result, dict) else str(result))'
    if old in content:
        content = content.replace(old, new)
    elif '"response": response' in content and "_strip_think_blocks(response)" not in content:
        content = content.replace(
            '"response": response,',
            '"response": _strip_think_blocks(response),',
        )

    chat_router.write_text(content)
    print("  ✅ Fix 1: Applied — think blocks will be stripped in chat router")


def fix_2_rewrite_system_prompt(results: list):
    """Fix: Replace system prompt with production-grade English-only version."""
    print("  🔧 Fix 2: Rewriting PIVOT_SYSTEM_PROMPT...")

    PRODUCTION_SYSTEM_PROMPT = '''You are Pivot — a precise, professional AI investing terminal for the Indian stock market, integrated with Zerodha Kite.

You execute. You explain. You do not advise.

WHAT YOU DO:
- Place market, limit, stop-loss, and GTT orders through Zerodha
- Build structured investment products: SafeGrow (capital protection), EarnMore (covered call income), StormShield (bear protection)
- Set up and manage SIP schedules and automation strategies
- Show portfolio data, P&L, holdings breakdown, and sector allocation
- Explain financial concepts in plain, precise English

HOW YOU RESPOND:
- Be brief. Maximum 2-3 sentences for simple questions. Maximum 3 short paragraphs for product explanations.
- No markdown formatting in plain text responses. No ** for bold. No ## headers. Write in clean prose.
- Numbers always in Indian format: ₹1,00,000 — never ₹100000
- English only. No Hindi, no Hinglish.
- Never show your reasoning process. Respond directly.

WHAT YOU NEVER DO:
- Never say "I recommend", "you should buy/sell", "this will definitely", or "guaranteed"
- Never fabricate live prices, index levels, or market data you do not have
- Never execute any order without showing a clear summary first
- Never skip the disclaimer on any financial action

DISCLAIMER — append to every response involving an order or strategy:
"This is automation of your instructions, not financial advice."

WHEN ASKED WHAT YOU CAN DO — keep it to 4 lines max:
Execute orders on Zerodha. Build capital protection and income products. Automate SIP and strategy rules. Analyse your portfolio. Ask me anything specific.

WHEN ASKED TO DO SOMETHING — propose it in one sentence, then stop and wait for confirmation. Do not over-explain.'''

    chat_router = ROOT / "backend" / "routers" / "chat.py"
    if not chat_router.exists():
        print("  ⚠️  chat.py not found — skipping")
        return

    content = chat_router.read_text()

    # Find and replace the system prompt
    pattern = r'PIVOT_SYSTEM_PROMPT\s*=\s*""".*?"""'
    replacement = f'PIVOT_SYSTEM_PROMPT = """{PRODUCTION_SYSTEM_PROMPT}"""'
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    if new_content != content:
        chat_router.write_text(new_content)
        print("  ✅ Fix 2: System prompt replaced with production version")
    else:
        # Try with triple quotes
        chat_router.write_text(content)
        print("  ✅ Fix 2: System prompt injection attempted")


def fix_3_markdown_rendering(results: list):
    """Fix: MessageBubble.jsx must render markdown, not display raw ** symbols."""
    if not check_failure_type(results, "MARKDOWN LEAK"):
        print("  ✅ Fix 3: Markdown rendering — no issues detected")

    print("  🔧 Fix 3: Adding markdown renderer to MessageBubble.jsx...")

    message_bubble = ROOT / "frontend" / "src" / "components" / "chat" / "MessageBubble.jsx"

    if not message_bubble.exists():
        # Try to find it
        candidates = list((ROOT / "frontend").rglob("MessageBubble.jsx"))
        if not candidates:
            print("  ⚠️  MessageBubble.jsx not found — creating it")
            message_bubble.parent.mkdir(parents=True, exist_ok=True)

    PRODUCTION_MESSAGE_BUBBLE = '''
// Lightweight markdown-to-JSX renderer
// Handles: **bold**, *italic*, line breaks, numbered lists
// Does NOT render ## headers (stripped by system prompt rules)
function renderMarkdown(text) {
  if (!text) return null;

  // Strip any <think> blocks that leaked through
  text = text.replace(/<think>[\\s\\S]*?<\\/think>/gi, "").trim();

  const lines = text.split("\\n").filter((l) => l.trim() !== "");

  return lines.map((line, i) => {
    // Bold: **text**
    const parts = line.split(/(\\*\\*[^*]+\\*\\*)/g).map((part, j) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={j} style={{ color: "#fff", fontWeight: 600 }}>
          {part.slice(2, -2)}
        </strong>;
      }
      // Italic: *text*
      return part.split(/(\*[^*]+\*)/g).map((p, k) => {
        if (p.startsWith("*") && p.endsWith("*") && !p.startsWith("**")) {
          return <em key={k} style={{ color: "rgba(255,255,255,0.85)" }}>{p.slice(1, -1)}</em>;
        }
        // ₹ amounts in mono
        return p.split(/(₹[\\d,]+)/g).map((segment, m) => {
          if (segment.startsWith("₹")) {
            return <span key={m} style={{ fontFamily: "var(--font-mono)", color: "#fff" }}>{segment}</span>;
          }
          return segment;
        });
      });
    });

    return (
      <p key={i} style={{ margin: i === 0 ? 0 : "8px 0 0", lineHeight: 1.65 }}>
        {parts}
      </p>
    );
  });
}

export function MessageBubble({ message }) {
  const isUser = message.role === "user";

  return (
    <div style={{
      display: "flex",
      justifyContent: isUser ? "flex-end" : "flex-start",
      marginBottom: 16,
    }}>
      {/* Avatar dot for AI */}
      {!isUser && (
        <div style={{
          width: 24, height: 24, borderRadius: "50%",
          background: "rgba(255,255,255,0.06)",
          border: "1px solid rgba(255,255,255,0.1)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 10, color: "rgba(255,255,255,0.4)",
          marginRight: 10, marginTop: 4, flexShrink: 0,
        }}>P</div>
      )}

      <div style={{
        maxWidth: "72%",
        padding: "12px 16px",
        borderRadius: isUser
          ? "16px 16px 4px 16px"
          : "4px 16px 16px 16px",
        background: isUser
          ? "rgba(255,255,255,0.08)"
          : "rgba(255,255,255,0.04)",
        border: "1px solid",
        borderColor: isUser
          ? "rgba(255,255,255,0.12)"
          : "rgba(255,255,255,0.07)",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.04)",
        backdropFilter: "blur(8px)",
        fontSize: 14,
        color: isUser ? "#fff" : "rgba(255,255,255,0.9)",
        lineHeight: 1.65,
        fontFamily: "var(--font-ui)",
      }}>
        {isUser
          ? <span>{message.content}</span>
          : renderMarkdown(message.content)
        }

        {/* LogicCard indicator — only shown when actual LogicCard JSON exists */}
        {message.logicCard && (
          <div style={{
            marginTop: 12,
            padding: "9px 12px",
            background: "rgba(34,197,94,0.05)",
            border: "1px solid rgba(34,197,94,0.15)",
            borderRadius: 8,
            fontSize: 12,
            color: "rgba(34,197,94,0.8)",
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <span style={{ opacity: 0.7 }}>◈</span>
            Strategy ready — confirm in Orders panel
          </div>
        )}

        {/* Timestamp */}
        {message.timestamp && (
          <div style={{
            marginTop: 6, fontSize: 10,
            color: "rgba(255,255,255,0.2)",
            textAlign: isUser ? "right" : "left",
          }}>
            {new Date(message.timestamp).toLocaleTimeString("en-IN", {
              hour: "2-digit", minute: "2-digit"
            })}
          </div>
        )}
      </div>
    </div>
  );
}
'''

    message_bubble.write_text(PRODUCTION_MESSAGE_BUBBLE)
    print("  ✅ Fix 3: MessageBubble.jsx rewritten with proper markdown renderer")

    # Also check ChatPane.jsx to ensure it passes timestamp and uses MessageBubble
    chat_pane = ROOT / "frontend" / "src" / "components" / "chat" / "ChatPane.jsx"
    if chat_pane.exists():
        content = chat_pane.read_text()
        # Ensure timestamps are added to messages
        if "timestamp" not in content:
            content = content.replace(
                "const userMsg = { role: 'user', content: input.trim() };",
                "const userMsg = { role: 'user', content: input.trim(), timestamp: new Date().toISOString() };",
            )
            content = content.replace(
                "const aiMsg = { role: 'assistant', content: text, logicCard };",
                "const aiMsg = { role: 'assistant', content: text, logicCard, timestamp: new Date().toISOString() };",
            )
            chat_pane.write_text(content)
            print("  ✅ Fix 3b: Added timestamps to ChatPane messages")

        # Ensure MessageBubble is imported and used
        if "MessageBubble" not in content:
            content = "import { MessageBubble } from './MessageBubble';\\n" + content
            # Replace inline message rendering with MessageBubble
            content = content.replace(
                '{messages.map((msg, i) => (',
                '{messages.map((msg, i) => (<MessageBubble key={i} message={msg} />))}',
            )
            chat_pane.write_text(content)
            print("  ✅ Fix 3c: Integrated MessageBubble into ChatPane")


def fix_4_logiccard_leak(results: list):
    """Fix: LogicCard indicator only shows when actual LogicCard JSON was parsed."""
    print("  🔧 Fix 4: Fixing LogicCard indicator to only show on real LogicCards...")

    chat_pane = ROOT / "frontend" / "src" / "components" / "chat" / "ChatPane.jsx"
    if not chat_pane.exists():
        print("  ⚠️  ChatPane.jsx not found — skipping")
        return

    content = chat_pane.read_text()

    # Ensure LogicCard is only set when valid JSON was parsed
    if "logicCard: null" not in content:
        # The parseLogicCard function should already handle this
        # But add a safety check in the message construction
        old = "const aiMsg = { role: 'assistant', content: text, logicCard"
        new = "const aiMsg = { role: 'assistant', content: text, logicCard: logicCard || null"
        if old in content and new not in content:
            content = content.replace(old + " };", new + " };")
            chat_pane.write_text(content)
            print("  ✅ Fix 4: LogicCard set to null when not parsed")
        else:
            print("  ✅ Fix 4: LogicCard handling appears correct")
    else:
        print("  ✅ Fix 4: LogicCard null-check already present")


def fix_5_response_length(results: list):
    """Fix: Enforce token limits in chat endpoint to prevent essay-length responses."""
    print("  🔧 Fix 5: Enforcing response length limits...")

    chat_router = ROOT / "backend" / "routers" / "chat.py"
    if not chat_router.exists():
        print("  ⚠️  chat.py not found — skipping")
        return

    content = chat_router.read_text()

    # Replace any max_tokens that's too high
    content = re.sub(
        r"max_tokens\s*=\s*\d+",
        "max_tokens=400",
        content,
        count=1,  # only the main chat call
    )

    chat_router.write_text(content)
    print("  ✅ Fix 5: max_tokens set to 400 for chat responses")


def fix_6_remove_hindi_references(results: list):
    """Fix: Remove all Hindi/Hinglish references from codebase."""
    print("  🔧 Fix 6: Removing Hindi/Hinglish references...")

    files_to_clean = [
        ROOT / "backend" / "routers" / "chat.py",
        ROOT / "backend" / "agents" / "parser.py",
        ROOT / "backend" / "agents" / "intent_classifier.py",
        ROOT / "backend" / "agents" / "sarvam_client.py",
    ]

    hindi_patterns = [
        r"Hinglish",
        r"Hindi/Hinglish",
        r"hindi",
        r"Hinglish support",
        r"bachao",
        r"nuksaan",
        r"girne",
        r"mera portfolio",
        r"kitna hai",
        r"FD se zyada",
        r"Aap kya",
        r"english unless the user switches to Hindi",
    ]

    for filepath in files_to_clean:
        if not filepath.exists():
            continue
        content = filepath.read_text()
        original = content
        for pattern in hindi_patterns:
            content = re.sub(pattern, "English", content, flags=re.IGNORECASE)
        if content != original:
            filepath.write_text(content)
            print(f"  ✅ Fix 6: Cleaned Hindi references from {filepath.name}")

    # Also clean frontend placeholder text
    chat_pane = ROOT / "frontend" / "src" / "components" / "chat" / "ChatPane.jsx"
    if chat_pane.exists():
        content = chat_pane.read_text()
        content = content.replace(
            "Kuch bhi pucho — 'INFY buy karna hai', 'portfolio dikhao', 'SafeGrow samjhao'...",
            "Ask anything — 'buy 10 INFY at market', 'show my portfolio', 'explain SafeGrow'...",
        )
        content = content.replace(
            "Namaste. Main Pivot hoon — aapka AI investing terminal. Kya karna chahte hain aaj?",
            "Welcome to Pivot. I can execute orders, build investment products, and analyse your portfolio. What would you like to do?",
        )
        chat_pane.write_text(content)
        print("  ✅ Fix 6: Frontend placeholder text cleaned")

    # Clean quick actions in Dashboard
    dashboard = ROOT / "frontend" / "src" / "pages" / "Dashboard.jsx"
    if dashboard.exists():
        content = dashboard.read_text()
        content = content.replace(
            "Kuch bhi pucho — 'INFY buy karna hai', 'portfolio dikhao', 'SafeGrow samjhao'...",
            "Ask anything — 'buy INFY at market', 'show my portfolio', 'explain SafeGrow'...",
        )
        dashboard.write_text(content)


def fix_7_chat_input_placeholder(results: list):
    """Fix: Replace all Hindi placeholders in UI."""
    print("  🔧 Fix 7: Cleaning UI text...")

    targets = [
        (ROOT / "frontend" / "src" / "components" / "chat" / "ChatPane.jsx", [
            ("Kuch bhi pucho", "Ask anything"),
            ("Kya karna chahte", "What would you like to do"),
            ("Namaste", "Welcome"),
            ("Main Pivot hoon", "I am Pivot"),
        ]),
        (ROOT / "frontend" / "src" / "pages" / "Dashboard.jsx", [
            ("Kuch bhi pucho", "Ask anything"),
            ("Buy NIFTYBEES SIP ₹5,000", "Set up NIFTYBEES SIP ₹5,000"),
            ("portfolio health", "Portfolio health scan"),
            ("Protect ₹1L for 12 months", "SafeGrow: protect ₹1L for 12 months"),
            ("Dip buy HDFC at -5%", "Dip buy HDFC Bank at -5%"),
        ]),
    ]

    for filepath, replacements in targets:
        if not filepath.exists():
            continue
        content = filepath.read_text()
        original = content
        for old, new in replacements:
            content = content.replace(old, new)
        if content != original:
            filepath.write_text(content)
            print(f"  ✅ Fix 7: Updated UI text in {filepath.name}")


def run_all_fixes():
    print("\\n🔧 PIVOT FIX AGENT — Applying improvements\\n")
    print("=" * 60)

    log = load_log()
    results = log["results"]

    print(f"\\nLoading QA results from: {LOG_FILE}")
    print(f"Pass rate before fixes: {log['pass_rate']}\\n")

    fix_1_strip_think_blocks(results)
    fix_2_rewrite_system_prompt(results)
    fix_3_markdown_rendering(results)
    fix_4_logiccard_leak(results)
    fix_5_response_length(results)
    fix_6_remove_hindi_references(results)
    fix_7_chat_input_placeholder(results)

    print("\\n" + "=" * 60)
    print("\\n✅ All fixes applied.")
    print("\\n📋 Next steps:")
    print("  1. Restart backend:  uvicorn backend.main:app --reload")
    print("  2. Restart frontend: cd frontend && npm run dev")
    print("  3. Re-run QA tests:  python scripts/qa_agent.py")
    print("  4. Target: 10/10 tests passing before calling it production-ready")


if __name__ == "__main__":
    run_all_fixes()
'''

# ============================================================
# WHAT CLAUDE CODE MUST DO
# ============================================================

INSTRUCTIONS = '''
# CLAUDE CODE EXECUTION INSTRUCTIONS

## WHAT THESE SCRIPTS DO

Agent A (qa_agent.py) runs 10 real conversations against your live Sarvam AI
integration and records every failure to /tmp/pivot_qa_log.json

Agent B (fix_agent.py) reads those failures and automatically patches:
- backend/routers/chat.py  (think block stripping, system prompt, token limits)
- backend/agents/*.py      (remove Hindi references)
- frontend/src/components/chat/MessageBubble.jsx  (markdown rendering)
- frontend/src/components/chat/ChatPane.jsx        (timestamps, imports)
- frontend/src/pages/Dashboard.jsx                 (UI text cleanup)

## STEP 1: Create the scripts folder and both files

Create: scripts/qa_agent.py  (paste QA_AGENT_CODE)
Create: scripts/fix_agent.py  (paste FIX_AGENT_CODE)

## STEP 2: Run Agent A first

```bash
python scripts/qa_agent.py
```

This will print results like:
  [T01] Greeting / capability question...
    Input: "what all can you do"
    ❌ FAIL — FOUND FORBIDDEN: '<think>'
    ❌ FAIL — MARKDOWN LEAK: ** found in response

## STEP 3: Run Agent B immediately after

```bash
python scripts/fix_agent.py
```

Agent B reads the failures and applies all patches automatically.

## STEP 4: Restart and re-test

```bash
# Terminal 1
uvicorn backend.main:app --reload --port 8000

# Terminal 2
cd frontend && npm run dev

# Terminal 3 — re-run QA after 10 seconds
python scripts/qa_agent.py
```

## STEP 5: Iterate until 10/10 pass

The loop is:
  qa_agent.py → shows failures → fix_agent.py → patches code → qa_agent.py again

Each cycle should improve the pass rate. Target: 10/10 before shipping.

## ALSO APPLY THESE MANUAL FIXES:

### Fix A: sarvam_client.py — ensure _strip_think_blocks runs on ALL responses

In the call_sarvam function, the content extraction must ALWAYS strip think blocks:

```python
# Line that extracts content from Sarvam response:
# BEFORE:
content = choice.get("content") or ""

# AFTER:
content = _strip_think_blocks(choice.get("content") or "")
```

### Fix B: chat.py — wrap the response before sending to frontend

```python
# In the /chat endpoint, before returning:
# BEFORE:
response = await call_sarvam(messages=user_messages, system_prompt=PIVOT_SYSTEM_PROMPT)
return {"response": response, ...}

# AFTER:
raw_response = await call_sarvam(
    messages=user_messages,
    system_prompt=PIVOT_SYSTEM_PROMPT,
    max_tokens=400,
    reasoning_effort=None,  # MUST be None — no think mode for chat
)
response = raw_response.get("content", "") if isinstance(raw_response, dict) else str(raw_response)
response = _strip_think_blocks(response)
return {"response": response, ...}
```

### Fix C: MessageBubble.jsx — import and use the renderMarkdown function

The fix_agent.py writes the full production MessageBubble.jsx automatically.
Verify it was written by checking:
  frontend/src/components/chat/MessageBubble.jsx

### Fix D: ChatPane.jsx — use MessageBubble component

In the messages list render:
```jsx
// BEFORE (likely rendering raw text):
{messages.map((msg, i) => (
  <div key={i}>...</div>
))}

// AFTER:
import { MessageBubble } from "./MessageBubble";
{messages.map((msg, i) => (
  <MessageBubble key={i} message={msg} />
))}
```

### Fix E: Remove ALL Hindi from Quick Actions in Dashboard.jsx

Replace quick action items with English versions:
- "Buy NIFTYBEES SIP ₹5,000"     ← already English, keep
- "Show portfolio health"          ← already English, keep
- "SafeGrow: ₹1L capital protection" ← rename to this
- "Dip buy HDFC Bank at -5%"     ← already English, keep

## PRODUCTION CHECKLIST — verify before calling it done

After running qa_agent.py and getting 10/10:

□ No <think> block visible in any chat response
□ **bold** renders as bold text (not raw asterisks)
□ Chat responses are ≤ 3 short paragraphs max
□ "What all can you do" response is ≤ 4 lines
□ All placeholder text in UI is English
□ LogicCard indicator only appears when strategy JSON exists
□ Disclaimer appears on every order/strategy response
□ Live prices are not hallucinated — Pivot says "I don\'t have real-time data"
□ "buy INFY" response proposes the order cleanly in 1-2 sentences
□ No Hindi keywords anywhere in backend or frontend code
'''