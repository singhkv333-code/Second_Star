"""Write every email template to a folder, with an index, for review.

    python3 mail_preview.py OUT_DIR

Uses the illustrative samples in mail_templates.samples(); sends nothing.
"""
from __future__ import annotations

import html
from pathlib import Path
import sys

import mail_templates


GALLERY = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pivot emails</title>
<style>
body{margin:0;font:16px/1.6 Inter,system-ui,sans-serif;color:#111312;background:#e9e5da}
.bar{position:sticky;top:0;z-index:2;background:#111312;color:#f3f0e7;padding:14px 24px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.bar h1{margin:0;font:600 22px Newsreader,Georgia,serif}.bar label{font-size:15px;display:flex;gap:6px;align-items:center;cursor:pointer}
nav{display:flex;flex-wrap:wrap;gap:6px 14px;padding:14px 24px;background:#f6f3eb;border-bottom:1px solid #d9d4c6}
nav a{color:#13687a;font-size:15px;text-transform:capitalize}
main{display:grid;gap:32px;padding:28px 24px 80px;justify-content:center}
section{background:#fff;border:1px solid #d9d4c6;border-radius:12px;overflow:hidden;width:min(720px,94vw)}
section header{padding:16px 20px;border-bottom:1px solid #e4dfd2}section h2{margin:0;font-size:18px}
section p{margin:4px 0 0;font-size:15px;color:#2a2e2c}.meta a{color:#13687a}
iframe{display:block;width:100%;border:0;height:600px;margin:0 auto;transition:width .2s}
body.phone iframe{width:390px}
</style>
<div class="bar"><h1>Pivot emails</h1>
<label><input type="checkbox" id="phone"> Phone width</label>
<label><input type="checkbox" id="dark"> Dark mode (Apple Mail style)</label></div>
<nav>{nav}</nav><main>{cards}</main>
<script>
const frames=[...document.querySelectorAll("iframe")];
const fit=f=>{try{f.style.height=f.contentDocument.documentElement.scrollHeight+"px"}catch(e){}};
const dark=on=>frames.forEach(f=>{try{const st=f.contentDocument.querySelector("style");
 if(!st.dataset.src)st.dataset.src=st.textContent;
 st.textContent=on?st.dataset.src.replace("@media (prefers-color-scheme: dark)","@media all"):st.dataset.src;}catch(e){}});
frames.forEach(f=>f.addEventListener("load",()=>{fit(f);dark(document.getElementById("dark").checked)}));
document.getElementById("phone").onchange=e=>{document.body.classList.toggle("phone",e.target.checked);setTimeout(()=>frames.forEach(fit),250)};
document.getElementById("dark").onchange=e=>dark(e.target.checked);
</script></html>"""


def main(out: str) -> None:
    root = Path(out)
    root.mkdir(parents=True, exist_ok=True)
    cards = []
    for kind, mail in mail_templates.samples().items():
        (root / f"{kind}.html").write_text(mail.html)
        (root / f"{kind}.txt").write_text(mail.text)
        cards.append(
            f'<section id="{kind}"><header><h2>{html.escape(mail.subject)}</h2>'
            f'<p>{html.escape(mail.preheader)}</p><p class="meta">{mail.category} · {kind} · '
            f'<a href="{kind}.html" target="_blank">open alone</a> · '
            f'<a href="{kind}.txt" target="_blank">plain text</a></p></header>'
            f'<iframe src="{kind}.html" title="{html.escape(mail.subject)}" loading="lazy"></iframe></section>')
    nav = "".join(f'<a href="#{k}">{k.replace("_", " ")}</a>' for k in mail_templates.samples())
    (root / "index.html").write_text(GALLERY.replace("{nav}", nav).replace("{cards}", "".join(cards)))
    print(f"wrote {len(cards)} templates to {root}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "mail_preview")
