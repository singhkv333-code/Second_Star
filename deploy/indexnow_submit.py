#!/usr/bin/env python3
"""Tell Bing, Yandex, Seznam and Naver (IndexNow) which public pages exist.

    python3 indexnow_submit.py                      # every URL in the sitemaps
    python3 indexnow_submit.py https://app.pivotnow.in/stock/TCS ...

Bing's index is what ChatGPT search and Copilot answer from, so a page Bing
has not crawled is a page those answers cannot cite. IndexNow asks it to
crawl now instead of whenever it next reads the sitemap. Google does not take
IndexNow; it reads the sitemap (submit that in Search Console once).

Ownership is proved by the key file the site serves at /indexnow-key.txt
(app/lib/seo/site.ts). Stdlib only, so it runs anywhere.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

SITE = "https://app.pivotnow.in"
KEY = "ae55aa1e736e0281a3d048b0bba846d3"
ENDPOINT = "https://api.indexnow.org/indexnow"
BATCH = 10_000  # the protocol's per-request limit


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "pivot-indexnow/1"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        return r.read().decode("utf-8")


def sitemap_urls() -> list[str]:
    index = fetch(f"{SITE}/sitemap.xml")
    urls: list[str] = []
    for sm in re.findall(r"<loc>([^<]+)</loc>", index):
        urls += re.findall(r"<loc>([^<]+)</loc>", fetch(sm))
    return [u.replace("&amp;", "&") for u in urls]


def submit(urls: list[str]) -> None:
    served = fetch(f"{SITE}/indexnow-key.txt").strip()
    if served != KEY:
        sys.exit(f"the site serves key {served!r}, not this script's; deploy first")
    host = SITE.split("//", 1)[1]
    for i in range(0, len(urls), BATCH):
        body = json.dumps({
            "host": host, "key": KEY, "keyLocation": f"{SITE}/indexnow-key.txt",
            "urlList": urls[i:i + BATCH],
        }).encode()
        req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
                print(f"{len(urls[i:i + BATCH])} URLs -> HTTP {r.status}")
        except urllib.error.HTTPError as e:
            # 202 = accepted pending key check; 422/403 name what is wrong.
            print(f"{len(urls[i:i + BATCH])} URLs -> HTTP {e.code} {e.read()[:200]!r}")


if __name__ == "__main__":
    targets = sys.argv[1:] or sitemap_urls()
    print(f"submitting {len(targets)} URLs for {SITE}")
    submit(targets)
