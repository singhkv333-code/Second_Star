"""Write every email template to a folder, with an index, for review.

    python3 mail_preview.py OUT_DIR

Uses the illustrative samples in mail_templates.samples(); sends nothing.
"""
from __future__ import annotations

import html
from pathlib import Path
import sys

import mail_templates


def main(out: str) -> None:
    root = Path(out)
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for kind, mail in mail_templates.samples().items():
        (root / f"{kind}.html").write_text(mail.html)
        (root / f"{kind}.txt").write_text(mail.text)
        rows.append(f'<li><a href="{kind}.html">{html.escape(mail.subject)}</a>'
                    f' <span>{mail.category} · {kind}</span></li>')
    (root / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Pivot emails</title>"
        "<style>body{font:16px/1.6 Inter,system-ui,sans-serif;margin:40px;color:#111312;"
        "background:#f6f3eb}span{color:#3b403e;font-size:14px;margin-left:8px}"
        "a{color:#13687a}</style><h1>Pivot emails</h1><ol>" + "".join(rows) + "</ol>")
    print(f"wrote {len(rows)} templates to {root}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "mail_preview")
