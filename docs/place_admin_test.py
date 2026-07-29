"""Place a diverse ~22-item paper test set from the admin account (user 2,
kvsingh171717@gmail.com). Drives the LIVE chat pipeline, then arms/deploys each
result via the real endpoints. Budget-guarded to stay under Rs5,00,000.

Categories: simple buys, conditional+scheduled agents, baskets/strategies,
opinion-market deployments. Writes a JSON + prints a summary.
"""
import json, time, sys
import httpx
from backend.auth.jwt_handler import create_access_token

BASE = "http://localhost:8000"
TOK = create_access_token(2, "kvsingh171717@gmail.com")
H = {"Authorization": f"Bearer {TOK}"}
c = httpx.Client(base_url=BASE, timeout=120)

BUDGET = 500_000
committed = 0.0        # estimated cash that will debit at market open
results = []

def mark(sym):
    try:
        from backend.paper.marks import get_mark_price
        m = get_mark_price(sym.upper())
        return float(m) if m else None
    except Exception:
        return None

def chat(msg, conv):
    r = c.post("/chat", json={"messages":[{"role":"user","content":msg}],
                              "conversation_id":conv}, headers=H)
    return r.json() if r.status_code == 200 else {"_err": r.status_code, "_body": r.text[:200]}

def log(cat, prompt, outcome, detail, est=0.0):
    results.append({"category":cat, "prompt":prompt, "outcome":outcome,
                    "detail":detail, "est_capital": round(est,0)})
    print(f"[{cat}] {outcome}  {prompt[:60]}  ~Rs{est:,.0f}  {detail[:80]}")

def est_cost(sym, qty):
    p = mark(sym) or 0
    return p * qty

# ── 1. SIMPLE BUYS (chat renders order card → /orders/register; rests→fills at open)
SIMPLE = [
    ("NIFTYBEES", 15), ("GOLDBEES", 40), ("INFY", 3), ("TCS", 2),
    ("RELIANCE", 5), ("HDFCBANK", 4), ("ITC", 10), ("SBIN", 8),
]
for sym, qty in SIMPLE:
    prompt = f"Buy {qty} {sym}."
    est = est_cost(sym, qty)
    if committed + est > BUDGET * 0.9:
        log("simple_buy", prompt, "SKIPPED_BUDGET", "would exceed budget", est); continue
    j = chat(prompt, f"admin_buy_{sym}")
    tools = j.get("tools_called")
    r = c.post("/orders/register", json={"symbol":sym,"exchange":"NSE",
               "transaction_type":"BUY","quantity":qty,"order_type":"MARKET"}, headers=H)
    if r.status_code < 300:
        st = r.json().get("status")
        committed += est
        log("simple_buy", prompt, "REGISTERED", f"chat_tools={tools} order_status={st}", est)
    else:
        log("simple_buy", prompt, "REG_FAIL", f"{r.status_code} {r.text[:120]}", 0)
    time.sleep(1)

# ── 2. AGENTS (chat propose_workflow → create + activate; armed, fire at their trigger)
AGENTS = [
    "Build me an agent that buys 10 NIFTYBEES when RSI drops below 30.",
    "Create an agent that buys 5 RELIANCE when the price crosses above 1350.",
    "Buy 20 GOLDBEES every weekday at 9:20 AM.",
    "Set up an agent to buy 3 TCS if it dips 5 percent in a day.",
    "Alert me when INFY crosses 1600, don't buy anything.",
    "Buy 15 NIFTYBEES every Monday morning.",
    "Make an agent that buys 8 ICICIBANK when its RSI goes below 35.",
]
for i, prompt in enumerate(AGENTS):
    j = chat(prompt, f"admin_agent_{i}")
    rd = j.get("raw_data") or {}
    steps = rd.get("steps")
    name = rd.get("name") or f"Admin agent {i}"
    tools = j.get("tools_called")
    if not steps:
        log("agent", prompt, "NO_DRAFT", f"tools={tools} hint={rd.get('_render_hint')}", 0)
        continue
    cr = c.post("/api/workflows", json={"name":name[:120],
                "description":(rd.get('description') or '')[:250], "steps":steps}, headers=H)
    if cr.status_code >= 300:
        log("agent", prompt, "CREATE_FAIL", f"{cr.status_code} {cr.text[:140]}", 0); continue
    wid = cr.json()["id"]
    av = c.post(f"/api/workflows/{wid}/activate", headers=H)
    st = av.json().get("status") if av.status_code < 300 else f"{av.status_code} {av.text[:100]}"
    log("agent", prompt, "ARMED" if av.status_code < 300 else "ACTIVATE_FAIL",
        f"wf={wid} status={st} steps={len(steps)}", 0)
    time.sleep(1)

# ── 3. BASKETS / STRATEGIES (chat build_strategy → save equity basket → trade)
BASKETS = [
    ("Make me a basket of auto stocks, around 40 thousand rupees.", 40000),
    ("Build a defensive basket of FMCG stocks for about 35 thousand.", 35000),
    ("Make a basket of stocks that profit from a good monsoon, 40 thousand.", 40000),
    ("Build me a strategy that benefits from momentum, around 40 thousand.", 40000),
]
for i,(prompt, cap) in enumerate(BASKETS):
    if committed + cap > BUDGET * 0.95:
        log("basket", prompt, "SKIPPED_BUDGET", "would exceed budget", cap); continue
    j = chat(prompt, f"admin_basket_{i}")
    rd = j.get("raw_data") or {}
    cons = rd.get("constituents") or []
    hint = rd.get("_render_hint")
    if not cons:
        log("basket", prompt, "NO_BASKET", f"tools={j.get('tools_called')} hint={hint}", 0)
        continue
    members = [{"symbol":x["symbol"], "weight":x.get("weight_pct",0)} for x in cons if x.get("symbol")]
    name = (rd.get("title") or f"Admin basket {i}")[:120]
    sv = c.post("/strategies/baskets", json={"name":name,"members":members,
               "weighting":"custom","capital_inr":cap}, headers=H)
    if sv.status_code >= 300:
        log("basket", prompt, "SAVE_FAIL", f"{sv.status_code} {sv.text[:140]}", 0); continue
    bid = sv.json()["id"]
    tr = c.post(f"/strategies/baskets/{bid}/trade", json={"capital_inr":cap}, headers=H)
    if tr.status_code < 300:
        committed += cap
        log("basket", prompt, "DEPLOYED",
            f"basket={bid} routed={tr.json().get('routed_to')} legs={tr.json().get('count')} names={[m['symbol'] for m in members]}", cap)
    else:
        log("basket", prompt, "TRADE_FAIL", f"{tr.status_code} {tr.text[:140]}", 0)
    time.sleep(1)

# ── 4. OPINION MARKETS (Views): place equity-basket expressions into paper;
#      arm the rest (option/pair) as register-not-execute workflows via /deploy.
try:
    lv = c.get("/api/views", headers=H)
    views = lv.json().get("items", []) if lv.status_code < 300 else []
    print(f"[opinion] {len(views)} views available")
    placed = armed = 0
    for v in views:
        vid = v.get("id"); vtitle = v.get("title","?")[:40]
        det = c.get(f"/api/views/{vid}", headers=H)
        if det.status_code >= 300: continue
        for e in det.json().get("expressions", []):
            eid = e.get("id"); tier = e.get("tier")
            label = f"'{vtitle}' [{tier}]"
            # 1) try to PLACE it as a share basket (fills at open)
            if committed < BUDGET * 0.95:
                pr = c.post(f"/api/views/expressions/{eid}/place", json={}, headers=H)
                if pr.status_code < 300:
                    body = pr.json()
                    cap = float(body.get("est_total") or 25000)
                    committed += cap; placed += 1
                    log("opinion_place", f"Deploy {label}", "PLACED",
                        f"routed={body.get('routed_to')} legs={body.get('count', len(body.get('legs',[])))}", cap)
                    continue
            # 2) not a share basket → ARM as a register-not-execute workflow
            dp = c.post(f"/api/views/expressions/{eid}/deploy",
                        json={"activate": True, "capital_inr": 25000}, headers=H)
            if dp.status_code < 300:
                body = dp.json(); armed += 1
                log("opinion_arm", f"Arm {label}", "ARMED",
                    f"wf={str(body.get('workflow_id'))[:8]} status={body.get('status')} steps={body.get('steps_count')}", 0)
            else:
                log("opinion_arm", f"Deploy {label}", "DEPLOY_FAIL", f"{dp.status_code} {dp.text[:110]}", 0)
    print(f"[opinion] placed={placed} armed={armed}")
except Exception as ex:
    import traceback; traceback.print_exc()
    log("opinion", "opinion markets", "ERROR", str(ex)[:140], 0)

# ── SUMMARY
print("\n================ SUMMARY ================")
by = {}
for r in results:
    by.setdefault(r["category"], {"n":0,"ok":0})
    by[r["category"]]["n"] += 1
    if r["outcome"] in ("REGISTERED","ARMED","DEPLOYED"):
        by[r["category"]]["ok"] += 1
for cat, s in by.items():
    print(f"  {cat}: {s['ok']}/{s['n']} succeeded")
print(f"  TOTAL committed (fills at open): ~Rs{committed:,.0f} of Rs{BUDGET:,.0f}")

out = "/private/tmp/claude-501/-Users-karanveersingh-Downloads-Second-Star/79511ab6-5824-4066-9d1e-cfc43a42fc39/scratchpad/admin_test_results.json"
with open(out,"w") as f:
    json.dump({"committed":committed,"budget":BUDGET,"items":results}, f, indent=2, default=str)
print("wrote", out)
