"""Static server for the Charto preview, with caching disabled.

`python3 -m http.server` lets Chrome cache js/*.js, so edits appear to have
no effect until a manual hard reload. This sends no-store on everything.

Run:  python3 charto/preview/serve.py     (serves this dir on :5173)
"""
from __future__ import annotations

import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 5173


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # quiet
        pass


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"charto preview on http://127.0.0.1:{PORT} (no-cache)")
    ThreadingHTTPServer(("127.0.0.1", PORT), NoCacheHandler).serve_forever()
