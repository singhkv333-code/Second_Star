"""Pivot's email kit: the one layout and the few parts every message is made of.

Every email Pivot sends is assembled here, so they all look like one company
wrote them. The parts are deliberately few: a heading, paragraphs, a button, a
table of facts, a code, a note, a divider, numbered steps and a data table.

HOW IT IS BUILT, AND WHY
------------------------
- Tables and inline styles, single column, 600px wide and fluid below that.
  This is the structure React Email and MJML both compile to, because Outlook
  for Windows still renders with Word and ignores most modern CSS. A <style>
  block carries only progressive extras: dark mode and the phone layout.
- Black and white, like the product. Every message opens on a masthead: the
  sign-in page's wave field (grain and vignette included) with the wordmark,
  the alien inside "Pivot", in white. Those are the only two images, hosted
  beside the chart app (chart/assets/email). The masthead's colour is
  set as well, and the wordmark's alt text is set in the brand serif, so a
  client that blocks images still shows a black band reading "Pivot".
- Readable everywhere: body text is 16px in near-black, secondary text is
  15px and still dark, and the footer is 14px in a colour that passes WCAG AA
  on the page background. No pills, no badges, no pale grey fine print.
- Dark mode: the light palette is warm off-white, not pure white, so clients
  that force-invert (Gmail apps) produce something calm. Clients that support
  prefers-color-scheme (Apple Mail, Outlook.com via [data-ogsc]) get the
  designed dark palette instead.
- Every value that reaches HTML goes through `esc`. Only this module creates
  `Markup`, so a template cannot inject markup by accident.
- Every message has a plain-text part built from the same blocks, for clients
  and filters that read text only.
"""
from __future__ import annotations

from dataclasses import dataclass
import html
from typing import Iterable, NamedTuple, Sequence

# ── the palette: ink, greys and bone, no colour (the brand's dark palette) ───
INK = "#0b0b0c"        # headings, values, the button, links, the rule beside a note
TEXT = "#232325"       # body copy
SOFT = "#3d3d40"       # labels and the footer: dark enough for AA at 14-15px
LINE = "#e3e2de"       # hairlines
PAGE = "#efeeea"       # behind the card
CARD = "#ffffff"       # the card
WELL = "#f5f4f1"       # the code panel, the table header, a note
LINK = INK             # underlined, so it reads as a link without a colour
BONE = "#fbfcfc"       # text on the masthead
MIST = "#cfcfcb"       # the masthead's second line
NOTE_BG = WELL

ASSETS = "https://app.pivotnow.in/assets/email"
MASTHEAD = f"{ASSETS}/masthead.jpg"          # 1200 x 560, the wave field
WORDMARK = f"{ASSETS}/wordmark-white.png"    # 480 x 181, white on clear
MAST_BG = "#0a0a0b"

SERIF = "Newsreader, Georgia, 'Times New Roman', serif"
SANS = ("Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "Helvetica, Arial, sans-serif")
MONO = "'SFMono-Regular', Menlo, Consolas, 'Liberation Mono', monospace"


class Markup(str):
    """HTML this module built. Anything else is text and is escaped."""


def esc(value) -> Markup:
    if isinstance(value, Markup):
        return value
    return Markup(html.escape("" if value is None else str(value), quote=True))


def _url(url: str) -> str:
    url = str(url or "").strip()
    if not (url.startswith("https://") or url.startswith("http://localhost")
            or url.startswith("mailto:")):
        raise ValueError("email links must be https or mailto")
    return url


class Block(NamedTuple):
    html: str
    text: str


class Hero(Block):
    """A headline that belongs on the masthead rather than in the card."""


@dataclass(frozen=True)
class Link:
    """An inline link inside a paragraph."""
    label: str
    url: str


def _inline(parts) -> tuple[str, str]:
    if isinstance(parts, (str, Link)):
        parts = [parts]
    h, t = [], []
    for part in parts:
        if isinstance(part, Link):
            url = _url(part.url)
            h.append(f'<a href="{esc(url)}" class="pv-link" style="color:{LINK};'
                     f'text-decoration:underline;">{esc(part.label)}</a>')
            target = url.removeprefix("mailto:").split("?")[0]
            t.append(part.label if part.label == target else f"{part.label} ({target})")
        else:
            h.append(esc(part))
            t.append(str(part))
    return "".join(h), "".join(t)


# ── the parts ───────────────────────────────────────────────────────────────

def heading(text: str) -> Block:
    return Block(
        f'<h1 class="pv-ink pv-h1" style="margin:0 0 18px;font-family:{SERIF};'
        f'font-size:30px;line-height:36px;font-weight:400;letter-spacing:-0.4px;'
        f'color:{INK};">{esc(text)}</h1>',
        f"{text}\n{'=' * min(len(text), 60)}")


def hero(title: str, line: str = "") -> Hero:
    """The headline for the moments that deserve one (a welcome, access, a new
    plan). `page` sets it on the masthead, under the wordmark, in the brand
    serif. Live text, so it survives blocked images."""
    sub = (f'<p style="margin:12px 0 0;font-family:{SANS};font-size:16px;line-height:25px;'
           f'color:{MIST};">{esc(line)}</p>') if line else ""
    return Hero(
        f'<h1 class="pv-h1" style="margin:64px 0 0;font-family:{SERIF};font-size:34px;'
        f'line-height:40px;font-weight:400;letter-spacing:-0.5px;color:{BONE};">{esc(title)}</h1>'
        f'{sub}',
        f"{title}\n{'=' * min(len(title), 60)}" + (f"\n{line}" if line else ""))


def subheading(text: str) -> Block:
    return Block(
        f'<h2 class="pv-ink" style="margin:28px 0 10px;font-family:{SANS};'
        f'font-size:17px;line-height:24px;font-weight:600;color:{INK};">{esc(text)}</h2>',
        f"\n{text}")


def para(*parts) -> Block:
    h, t = _inline(list(parts))
    return Block(
        f'<p class="pv-text" style="margin:0 0 16px;font-family:{SANS};font-size:16px;'
        f'line-height:26px;color:{TEXT};">{h}</p>', t)


def button(label: str, url: str) -> Block:
    url = _url(url)
    # A table cell carries the colour so Outlook paints it; the padding sits on
    # the link so the whole button is clickable everywhere else.
    return Block(
        '<table role="presentation" border="0" cellpadding="0" cellspacing="0" '
        'style="margin:8px 0 24px;border-collapse:separate;"><tr>'
        f'<td class="pv-btn" bgcolor="{INK}" style="border-radius:8px;background:{INK};">'
        f'<a href="{esc(url)}" class="pv-btn-a" target="_blank" style="display:inline-block;'
        f'padding:13px 24px;font-family:{SANS};font-size:16px;line-height:20px;'
        f'font-weight:600;color:{CARD};text-decoration:none;border-radius:8px;">'
        f'{esc(label)}</a></td></tr></table>',
        f"{label}: {url}")


def facts(rows: Sequence[tuple[str, object]]) -> Block:
    """Label and value, one per row. Values are what the reader came for, so
    they carry the weight; labels stay dark enough to read."""
    rows = [(k, v) for k, v in rows if v not in (None, "")]
    cells = []
    for i, (label, value) in enumerate(rows):
        top = f"border-top:1px solid {LINE};" if i == 0 else ""
        cells.append(
            f'<tr><td class="pv-soft pv-rule" style="{top}border-bottom:1px solid {LINE};'
            f'padding:12px 16px 12px 0;font-family:{SANS};font-size:15px;line-height:22px;'
            f'color:{SOFT};vertical-align:top;width:42%;">{esc(label)}</td>'
            f'<td class="pv-ink pv-rule" style="{top}border-bottom:1px solid {LINE};'
            f'padding:12px 0;font-family:{SANS};font-size:16px;line-height:22px;'
            f'font-weight:600;color:{INK};vertical-align:top;'
            f'font-variant-numeric:tabular-nums;">{esc(value)}</td></tr>')
    return Block(
        '<table role="presentation" width="100%" border="0" cellpadding="0" '
        'cellspacing="0" style="margin:4px 0 24px;border-collapse:collapse;">'
        + "".join(cells) + "</table>",
        "\n".join(f"{k}: {v}" for k, v in rows))


def code(value: str) -> Block:
    return Block(
        '<table role="presentation" width="100%" border="0" cellpadding="0" '
        'cellspacing="0" style="margin:4px 0 24px;"><tr>'
        f'<td class="pv-well pv-ink" align="center" style="background:{WELL};'
        f'border:1px solid {LINE};border-radius:10px;padding:18px 12px;font-family:{MONO};'
        f'font-size:30px;line-height:36px;font-weight:600;letter-spacing:6px;color:{INK};">'
        f'{esc(value)}</td></tr></table>',
        f"\n    {value}\n")


def note(*parts) -> Block:
    """Something the reader must not miss: a warning, an exception, a limit.
    An ink rule on a quiet panel, never a coloured badge."""
    h, t = _inline(list(parts))
    return Block(
        '<table role="presentation" width="100%" border="0" cellpadding="0" '
        'cellspacing="0" style="margin:4px 0 24px;"><tr>'
        f'<td class="pv-note pv-text" style="border-left:3px solid {INK};'
        f'background:{NOTE_BG};padding:14px 18px;font-family:{SANS};font-size:15px;'
        f'line-height:24px;color:{TEXT};">{h}</td></tr></table>',
        f"Note: {t}")


def divider() -> Block:
    return Block(
        '<table role="presentation" width="100%" border="0" cellpadding="0" '
        'cellspacing="0" style="margin:8px 0 24px;"><tr>'
        f'<td class="pv-rule" style="border-top:1px solid {LINE};font-size:0;'
        'line-height:0;height:1px;">&nbsp;</td></tr></table>',
        "-" * 40)


def steps(items: Iterable[tuple[str, str]]) -> Block:
    """Numbered steps: a short title and one sentence each."""
    rows, text = [], []
    for n, (title, body) in enumerate(items, 1):
        rows.append(
            '<tr><td class="pv-ink" style="width:34px;vertical-align:top;padding:2px 0 18px;'
            f'font-family:{SERIF};font-size:22px;line-height:24px;color:{INK};">{n}</td>'
            f'<td style="vertical-align:top;padding:0 0 18px;">'
            f'<p class="pv-ink" style="margin:0 0 4px;font-family:{SANS};font-size:16px;'
            f'line-height:24px;font-weight:600;color:{INK};">{esc(title)}</p>'
            f'<p class="pv-text" style="margin:0;font-family:{SANS};font-size:15px;'
            f'line-height:24px;color:{TEXT};">{esc(body)}</p></td></tr>')
        text.append(f"{n}. {title}\n   {body}")
    return Block(
        '<table role="presentation" width="100%" border="0" cellpadding="0" '
        'cellspacing="0" style="margin:8px 0 12px;">' + "".join(rows) + "</table>",
        "\n".join(text))


def bullets(items: Iterable) -> Block:
    """A short list. Each item is text, or a list of text and Links."""
    rows, text = [], []
    for item in items:
        h, t = _inline(item if isinstance(item, (list, tuple)) else [item])
        rows.append(
            '<tr><td class="pv-ink" style="width:22px;vertical-align:top;padding:0 0 10px;'
            f'font-family:{SANS};font-size:16px;line-height:26px;color:{INK};">&#8226;</td>'
            f'<td class="pv-text" style="vertical-align:top;padding:0 0 10px;font-family:{SANS};'
            f'font-size:16px;line-height:26px;color:{TEXT};">{h}</td></tr>')
        text.append(f"- {t}")
    return Block(
        '<table role="presentation" width="100%" border="0" cellpadding="0" '
        'cellspacing="0" style="margin:0 0 14px;">' + "".join(rows) + "</table>",
        "\n".join(text))


def table(headers: Sequence[str], rows: Sequence[Sequence[object]],
          align: Sequence[str] | None = None) -> Block:
    """A small data table: a watchlist, invoice lines. Numbers align right."""
    align = list(align or ["left"] + ["right"] * (len(headers) - 1))
    head = "".join(
        f'<th class="pv-well pv-soft" align="{a}" style="background:{WELL};'
        f'padding:10px 12px;font-family:{SANS};font-size:14px;line-height:20px;'
        f'font-weight:600;color:{SOFT};text-align:{a};border-bottom:1px solid {LINE};">'
        f'{esc(h)}</th>' for h, a in zip(headers, align))
    body = "".join(
        "<tr>" + "".join(
            f'<td class="pv-ink pv-rule" align="{a}" style="padding:11px 12px;'
            f'border-bottom:1px solid {LINE};font-family:{SANS};font-size:15px;'
            f'line-height:22px;color:{INK};text-align:{a};'
            f'font-variant-numeric:tabular-nums;{"font-weight:600;" if i == 0 else ""}">'
            f'{esc(c)}</td>' for i, (c, a) in enumerate(zip(row, align))) + "</tr>"
        for row in rows)
    text = [" | ".join(map(str, headers))] + [" | ".join(map(str, r)) for r in rows]
    return Block(
        '<table role="presentation" width="100%" border="0" cellpadding="0" '
        f'cellspacing="0" style="margin:4px 0 24px;border-collapse:collapse;'
        f'border:1px solid {LINE};">'
        f"<tr>{head}</tr>{body}</table>",
        "\n".join(text))


# ── the page ────────────────────────────────────────────────────────────────

_STYLE = f"""
:root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
body {{ margin:0; padding:0; width:100% !important; -webkit-text-size-adjust:100%; }}
a.pv-btn-a:hover {{ opacity:.88; }}
@media only screen and (max-width:620px) {{
  .pv-card {{ padding:30px 22px !important; }}
  .pv-mast {{ padding:24px 22px !important; }}
  .pv-h1 {{ font-size:26px !important; line-height:32px !important; }}
  .pv-shell {{ padding:20px 10px !important; }}
}}
@media (prefers-color-scheme: dark) {{
  .pv-page, body {{ background:#070708 !important; }}
  .pv-frame {{ border-color:#262628 !important; }}
  .pv-card {{ background:#121213 !important; }}
  .pv-ink {{ color:#f4f4f2 !important; }}
  .pv-text {{ color:#dcdcd8 !important; }}
  .pv-soft {{ color:#c9c9c4 !important; }}
  .pv-rule {{ border-color:#262628 !important; }}
  .pv-well {{ background:#1b1b1d !important; border-color:#262628 !important; }}
  .pv-note {{ background:#1b1b1d !important; border-left-color:#f4f4f2 !important; }}
  .pv-link {{ color:#f4f4f2 !important; }}
  .pv-btn {{ background:#f4f4f2 !important; }}
  .pv-btn-a {{ color:#0b0b0c !important; }}
}}
[data-ogsc] .pv-ink {{ color:#f4f4f2 !important; }}
[data-ogsc] .pv-text {{ color:#dcdcd8 !important; }}
[data-ogsc] .pv-soft {{ color:#c9c9c4 !important; }}
[data-ogsc] .pv-link {{ color:#f4f4f2 !important; }}
[data-ogsc] .pv-btn-a {{ color:#0b0b0c !important; }}
[data-ogsb] .pv-btn {{ background:#f4f4f2 !important; }}
"""


@dataclass(frozen=True)
class Footer:
    """Why the reader got this, and what they can do about it.

    `reason` is one sentence in plain words. `manage` is the place to change
    it, when there is one. `market` adds the analysis-not-advice line, for any
    email that carries market content."""
    reason: str
    manage: Link | None = None
    market: bool = False


COMPANY_LINE = "Pivot Tech, India"
SITE = "https://www.pivotnow.in"


def page(*, subject: str, preheader: str, blocks: Sequence[Block],
         footer: Footer, sender: str) -> tuple[str, str]:
    """The whole email, HTML and plain text, from its blocks."""
    lines_html, lines_text = [], []

    def line(*parts):
        h, t = _inline(list(parts))
        lines_html.append(
            f'<p class="pv-soft" style="margin:0 0 10px;font-family:{SANS};font-size:14px;'
            f'line-height:22px;color:{SOFT};">{h}</p>')
        lines_text.append(t)

    line(footer.reason, *([" ", footer.manage] if footer.manage else []))
    if footer.market:
        line("Pivot provides analysis and tools, not financial advice. "
             "Markets carry risk; past or simulated results do not promise future ones.")
    line("Questions? Reply to this email or write to ", Link(sender, f"mailto:{sender}"), ".")
    line(COMPANY_LINE, " · ", Link("pivotnow.in", SITE))

    # The preheader is the line inboxes show after the subject. The trailing
    # spacers stop clients from padding it with the first words of the body.
    spacer = "&#8199;&#65279;&#847;" * 40
    # A headline goes up on the masthead; everything else is the card.
    lead = "".join(b.html for b in blocks if isinstance(b, Hero))
    body = "".join(b.html for b in blocks if not isinstance(b, Hero))
    html_doc = f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no, date=no, address=no, email=no">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{esc(subject)}</title>
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
<!--[if !mso]><!--><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&amp;family=Newsreader:opsz,wght@6..72,400&amp;display=swap" rel="stylesheet"><!--<![endif]-->
<style>{_STYLE}</style>
</head>
<body class="pv-page" style="margin:0;padding:0;background:{PAGE};">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{PAGE};opacity:0;">{esc(preheader)}{spacer}</div>
<table role="presentation" class="pv-page" width="100%" border="0" cellpadding="0" cellspacing="0" style="background:{PAGE};">
<tr><td class="pv-shell" align="center" style="padding:36px 16px;">
<!--[if mso]><table role="presentation" width="600" border="0" cellpadding="0" cellspacing="0"><tr><td><![endif]-->
<table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="max-width:600px;">
<tr><td>
<table role="presentation" class="pv-frame" width="100%" border="0" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};border-radius:14px;border-collapse:separate;overflow:hidden;">
<tr><td class="pv-mast" background="{MASTHEAD}" bgcolor="{MAST_BG}" style="background-color:{MAST_BG};background-image:url('{MASTHEAD}');background-size:cover;background-position:center;border-radius:13px 13px 0 0;padding:{"34px 44px 40px" if lead else "26px 44px"};">
<a href="{SITE}" style="text-decoration:none;"><img src="{WORDMARK}" width="{120 if lead else 96}" height="{45 if lead else 36}" alt="Pivot" style="display:block;border:0;outline:none;text-decoration:none;width:{120 if lead else 96}px;height:auto;font-family:{SERIF};font-size:26px;line-height:30px;color:{BONE};"></a>
{lead}
</td></tr>
<tr><td class="pv-card" style="background:{CARD};border-radius:0 0 13px 13px;padding:40px 44px;">
{body}
</td></tr>
</table>
</td></tr>
<tr><td style="padding:24px 6px 0;">
{"".join(lines_html)}
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr>
</table>
</body>
</html>"""
    text = "\n\n".join(b.text for b in blocks) + "\n\n--\n" + "\n".join(lines_text) + "\n"
    return html_doc, text
