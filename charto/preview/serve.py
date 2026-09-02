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

PORT = int(os.environ.get("CHARTO_PREVIEW_PORT", "5173"))

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
# …and the data server, for the same reason. The company page's API base is
# inlined into its CLIENT bundle at build time, so it has to be an address the
# browser can reach in every deployment — which means a relative "/api", which
# means this origin has to serve it. nginx already does exactly this on the VM.
DATA_ORIGIN = os.environ.get("CHARTO_DATA_ORIGIN", "http://127.0.0.1:5174")
PROXY_PREFIXES = ("/stock/", "/_next/", "/__nextjs", "/api/",
                  "/paper", "/strategies")


def _upstream(path: str) -> str | None:
    """Which server answers `path`, or None to serve it from this directory.

    The paper book is the one route that SPLITS: the PAGE `/paper` is rendered
    by the company app, and its data — `/paper/summary`, `/paper/holdings`,
    `/paper/fills` — comes from the dataserver, same prefix, different server.
    nginx draws that line with two exact-match locations; this draws the same
    line, because the whole point of this proxy is that dev and the VM disagree
    about nothing.
    """
    head = path.split("?", 1)[0]
    if head in ("/paper", "/paper/"):
        return COMPANY_ORIGIN
    if head.startswith("/paper/") or head == "/strategies" \
            or head.startswith("/strategies/") or head.startswith("/api/"):
        return DATA_ORIGIN
    if head.startswith(("/stock/", "/_next/", "/__nextjs")):
        return COMPANY_ORIGIN
    return None


class NoCacheHandler(SimpleHTTPRequestHandler):
    # Set while relaying, so the no-store below applies to the LOCAL files
    # this server owns and not to a proxied response. Without it every
    # relayed answer carried two Cache-Control headers — the upstream's and
    # this one — and the company page's whole cache policy was cancelled by
    # a rule that exists to stop Chrome holding stale chart JS.
    _relaying = False

    def end_headers(self) -> None:
        if not self._relaying:
            self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # quiet
        pass

    def do_GET(self) -> None:                      # noqa: N802
        if _upstream(self.path):
            return self._proxy()
        return super().do_GET()

    def do_HEAD(self) -> None:                     # noqa: N802
        if _upstream(self.path):
            return self._proxy()
        return super().do_HEAD()

    def do_POST(self) -> None:                     # noqa: N802
        """Writes, which this server used to have no way to relay at all.

        Nothing behind the old prefixes ever posted, so GET and HEAD were the
        whole surface. The paper book posts — cancelling a resting order,
        pausing a strategy — and without this those buttons 501'd in dev and
        worked on the VM, which is the worst possible split.
        """
        if _upstream(self.path):
            return self._proxy()
        return self.send_error(405, "POST is only proxied, not served")

    def _proxy(self) -> None:
        """Hand one request to the company app and relay what comes back.

        Deliberately dumb: no rewriting, no caching, no header surgery beyond
        dropping hop-by-hop ones. This exists so DEV has the same single
        origin production has; nginx does the real job on the VM.
        """
        target = _upstream(self.path) or COMPANY_ORIGIN
        url = target + self.path
        self._relaying = True
        # A POST carries a body and a content type, and an Authorization header
        # is what makes the paper book the user's rather than nobody's — it was
        # never in this list because nothing proxied here had ever needed one.
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=payload, method=self.command)
        for h in ("Accept", "Accept-Language", "Cookie", "User-Agent",
                  "Referer", "RSC", "Next-Router-State-Tree",
                  "Authorization", "Content-Type"):
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
            who = ("The data server" if target == DATA_ORIGIN
                   else "The company page")
            body = (f"{who} is not running.\n\n"
                    f"Expected at {target}\n{exc}").encode()
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
