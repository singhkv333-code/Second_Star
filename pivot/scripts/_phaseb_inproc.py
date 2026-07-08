"""Phase B in-process runner: drive ChatService.handle() directly (no server).
Reuses the prompt battery from _phaseb_runner.py."""
import os, sys, json, uuid, asyncio, warnings, traceback
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _phaseb_runner import PROMPTS

from backend.routers.chat import _chat_service
from backend.services.chat_service import UserContext
from backend.database import SessionLocal


async def main(out):
    results = []
    for i, (cat, p) in enumerate(PROMPTS):
        db = SessionLocal()
        ctx = UserContext(user_id=1, kite_token=None, db=db, holdings=[])
        conv = f"s_{uuid.uuid4().hex[:10]}"
        try:
            turn = await _chat_service.handle(
                p, conv, ctx, history_override=[], mode_override=None, editor_draft=None)
            raw = turn.raw_data or {}
            rec = {"i": i, "category": cat, "prompt": p,
                   "response": turn.response or "",
                   "tools_called": list(turn.tools_called or []),
                   "render_hint": raw.get("_render_hint") if isinstance(raw, dict) else None,
                   "raw_keys": sorted(raw.keys())[:25] if isinstance(raw, dict) else None,
                   "sanitised": getattr(turn, "sanitised", None)}
        except Exception as e:
            rec = {"i": i, "category": cat, "prompt": p,
                   "response": f"<<ERROR: {e}>>", "error": traceback.format_exc()[:800]}
        finally:
            db.close()
        results.append(rec)
        print(f"[{i+1:02d}/{len(PROMPTS)}] {cat:9} tools={rec.get('tools_called')} "
              f"hint={rec.get('render_hint')}", flush=True)
    json.dump(results, open(out, "w"), indent=2)
    print(f"\nwrote {len(results)} -> {out}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/home/azureuser/phaseB_run.json"
    asyncio.run(main(out))
