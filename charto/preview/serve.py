"""Static server for the Charto preview, with caching disabled.

`python3 -m http.server` lets Chrome cache js/*.js, so edits appear to have
no effect until a manual hard reload. This sends no-store on everything.

Run:  python3 charto/preview/serve.py     (serves this dir on :5173)
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 5173

# The company page runs as its own Next app (charto/web on :5175) and is
# reached THROUGH this server rather than beside it.
#
# It used to be linked as an absolute http://localhost:5175/stock/X, which is
# a different origin: a second port in the address bar, no shared cookie, no
# shared theme, and a link that is simply dead anywhere that is not this
# laptop. One origin is the whole point — the deployed site proxies the same
# two prefixes from nginx, so the markup that works here works there with no
# build-time switch.
COMPANY_ORIGIN = os.environ.get("CHARTO_COMPANY_ORIGIN", "http://127.0.0.1:5175")
PROXY_PREFIXES = ("/stock/", "/_next/", "/__nextjs")


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # quiet
        pass

    def do_GET(self) -> None:                      # noqa: N802
        if self.path.startswith(PROXY_PREFIXES):
            return self._proxy()
        return super().do_GET()

    def do_HEAD(self) -> None:                     # noqa: N802
        if self.path.startswith(PROXY_PREFIXES):
            return self._proxy()
        return super().do_HEAD()

    def _proxy(self) -> None:
        """Hand one request to the company app and relay what comes back.

        Deliberately dumb: no rewriting, no caching, no header surgery beyond
        dropping hop-by-hop ones. This exists so DEV has the same single
        origin production has; nginx does the real job on the VM.
        """
        url = COMPANY_ORIGIN + self.path
        req = urllib.request.Request(url, method=self.command)
        for h in ("Accept", "Accept-Language", "Cookie", "User-Agent",
                  "Referer", "RSC", "Next-Router-State-Tree"):
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as up:
                body, status, headers = up.read(), up.status, up.headers
        except urllib.error.HTTPError as e:        # 404s and friends still render
            body, status, headers = e.read(), e.code, e.headers
        except Exception as exc:                   # noqa: BLE001
            # Say WHICH server is down. "connection refused" in a browser
            # console next to a localhost:5173 URL sends you looking at the
            # wrong process entirely.
            body = (f"The company page is not running.\n\n"
                    f"Start it with: charto/web/start.sh   ({COMPANY_ORIGIN})\n"
                    f"{exc}").encode()
            status, headers = 502, None
        self.send_response(status)
        if headers is not None:
            for k, v in headers.items():
                if k.lower() in ("connection", "keep-alive", "transfer-encoding",
                                 "content-encoding", "content-length"):
                    continue
                self.send_header(k, v)
        else:
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"charto preview on http://127.0.0.1:{PORT} (no-cache)")
    ThreadingHTTPServer(("127.0.0.1", PORT), NoCacheHandler).serve_forever()
