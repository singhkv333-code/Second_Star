"""Phase B: run the enrichment-capability prompt battery against live chat :8001.
Writes a transcript JSON for the judging workflow. English only (no Hinglish)."""
import sys, uuid, json, time, httpx

BASE = "http://localhost:8001"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/home/azureuser/phaseB_run.json"

PROMPTS = [
    # sector / industry
    ("sector", "what sector is Reliance in?"),
    ("sector", "which industry does Sun Pharma belong to?"),
    ("sector", "what sector and industry is Tata Steel in?"),
    # profile / business
    ("profile", "what does Infosys do?"),
    ("profile", "give me a profile of HDFC Bank"),
    ("profile", "what business is ITC in?"),
    ("profile", "tell me about Bajaj Finance"),
    # promoter holding
    ("promoter", "promoter holding of Asian Paints"),
    ("promoter", "how much do promoters own in Bajaj Finance?"),
    ("promoter", "is L&T promoter-owned or mostly institutional?"),
    ("promoter", "who are the promoters of Reliance and how much do they hold?"),
    # sector screening
    ("screen", "show me some good IT stocks"),
    ("screen", "cheap banking stocks with low PE"),
    ("screen", "top FMCG companies by ROE"),
    # comparison
    ("compare", "compare TCS and Infosys"),
    ("compare", "HDFC Bank vs ICICI Bank — fundamentals and promoter holding"),
    ("compare", "Reliance vs ONGC — which sector and who is more promoter-held?"),
    # strategy grounded in sector / profile
    ("strategy", "build me a strategy to buy IT stocks when they dip 5%"),
    ("strategy", "I want to invest in the pharma sector through a weekly SIP"),
    ("strategy", "suggest an options strategy on a large-cap with high promoter holding"),
    # single-stock analysis
    ("analysis", "analyse Reliance"),
    ("analysis", "should I buy ITC?"),
    ("analysis", "deep dive on Bajaj Finance"),
    # edge cases
    ("edge", "tell me about Snehaa Organics"),
    ("edge", "what sector is Wakanda Vibranium Ltd in?"),
    ("edge", "profile of Fluidclean Industries"),
    # leakage probes (option/strategy internal identifiers must not leak)
    ("leak", "build a bull put spread on NIFTY"),
    ("leak", "set up an iron condor on BANKNIFTY"),
    ("leak", "covered call on RELIANCE"),
    ("leak", "suggest an option strategy if I'm bullish on Infosys"),
    # error / user-facing robustness probes
    ("robust", "what's the PE of XYZ123FAKE?"),
    ("robust", "compare Reliance with a fake company called Zzzed Ltd"),
    ("robust", "buy 10 INFY when RSI goes below 30 and sell at 8% profit"),
    ("robust", "analyse NIFTY"),
]


def main():
    results = []
    with httpx.Client(timeout=150.0) as c:
        email = f"phaseb_{uuid.uuid4().hex[:8]}@pivoteval.com"
        tok = c.post(f"{BASE}/auth/register", json={
            "email": email, "password": "password123", "full_name": "Phase B"}).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        for i, (cat, p) in enumerate(PROMPTS):
            conv = f"s_{uuid.uuid4().hex[:10]}"
            t0 = time.time()
            try:
                r = c.post(f"{BASE}/chat", headers=H, json={
                    "messages": [{"role": "user", "content": p}],
                    "conversation_id": conv, "include_portfolio_context": False})
                b = r.json()
                raw = b.get("raw_data") or {}
                rec = {
                    "i": i, "category": cat, "prompt": p,
                    "response": b.get("response") or "",
                    "tools_called": b.get("tools_called"),
                    "render_hint": raw.get("_render_hint") if isinstance(raw, dict) else None,
                    "raw_keys": sorted(raw.keys())[:25] if isinstance(raw, dict) else None,
                    "has_logiccard": bool(b.get("logiccard")),
                    "latency_ms": int((time.time() - t0) * 1000),
                }
            except Exception as e:
                rec = {"i": i, "category": cat, "prompt": p,
                       "response": f"<<HTTP ERROR: {e}>>", "error": str(e),
                       "latency_ms": int((time.time() - t0) * 1000)}
            results.append(rec)
            print(f"[{i+1:02d}/{len(PROMPTS)}] {cat:9} {rec.get('latency_ms')}ms "
                  f"tools={rec.get('tools_called')} hint={rec.get('render_hint')}", flush=True)
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nwrote {len(results)} turns -> {OUT}")


if __name__ == "__main__":
    main()
