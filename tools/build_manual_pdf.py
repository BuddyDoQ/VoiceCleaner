"""Print the user manual (docs/guide/VoiceCleaner-User-Manual.html) to PDF.

The HTML manual is the source. This lays it out for paper: a cover, a contents page with
page numbers, a running header with the chapter name, page numbers, the notes at the foot
of the page that cites them, and the Day pictures only. It needs WeasyPrint, whose Pango
library is easiest to get on a Mac:

    brew install pango
    python3 -m venv ~/.venvs/weasyprint && ~/.venvs/weasyprint/bin/pip install weasyprint
    ~/.venvs/weasyprint/bin/python tools/build_manual_pdf.py [manual.html] [out.pdf]

Writes docs/guide/VoiceCleaner-User-Manual.pdf by default; tools/release.py attaches it to
every release. Rebuild it whenever the HTML manual changes.
"""
from __future__ import annotations

import html
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "guide" / "VoiceCleaner-User-Manual.html"
OUT = ROOT / "docs" / "guide" / "VoiceCleaner-User-Manual.pdf"


def text_of(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def one(pattern: str, source: str, what: str, flags=re.S) -> re.Match:
    m = re.search(pattern, source, flags)
    if not m:
        raise SystemExit(f"build_manual_pdf: could not find {what} in the manual")
    return m


def waveform_svg(bars: int = 96) -> str:
    """The cover's sound wave: purple (original) running into blue (enhanced)."""
    w, h, gap = 600.0, 110.0, 6.25
    parts = []
    for i in range(bars):
        x = i / (bars - 1)
        env = 0.35 + 0.65 * abs(math.sin(x * math.pi * 3.1 + 0.4)) * (0.55 + 0.45 * math.sin(x * 23.0) ** 2)
        amp = max(0.08, min(1.0, env * (0.75 + 0.25 * math.sin(i * 1.7))))
        bh = amp * h
        parts.append(f'<rect x="{i * gap:.2f}" y="{(h - bh) / 2:.2f}" width="2.6" height="{bh:.2f}" rx="1.3"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            '<defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="0">'
            '<stop offset="0" stop-color="#a78bfa"/><stop offset=".45" stop-color="#8b5cf6"/>'
            '<stop offset=".55" stop-color="#60a5fa"/><stop offset="1" stop-color="#3b82f6"/></linearGradient></defs>'
            f'<g fill="url(#g)">{"".join(parts)}</g></svg>')


def footnotes_inline(body: str) -> str:
    """Move each chapter note to where it is cited, as a WeasyPrint page footnote."""
    notes = {}
    for m in re.finditer(r'<li id="fn(\d+)"[^>]*>(.*?)</li>', body, re.S):
        notes[m.group(1)] = re.sub(r'\s*<a class="fn-back"[^>]*>.*?</a>', "", m.group(2), flags=re.S).strip()
    body = re.sub(r'<aside class="footnotes">.*?</aside>', "", body, flags=re.S)

    def call(m):
        n = m.group(1)
        if n not in notes:
            raise SystemExit(f"build_manual_pdf: footnote {n} is cited but has no note")
        return f'<span class="pfn">{notes.pop(n)}</span>'

    body = re.sub(r'<sup class="fn-call" id="fnref(\d+)">.*?</sup>', call, body, flags=re.S)
    if notes:
        raise SystemExit(f"build_manual_pdf: notes never cited: {sorted(notes)}")
    return body


def build(src: Path) -> str:
    page = src.read_text(encoding="utf-8")
    style = one(r"<style>(.*?)</style>", page, "the stylesheet").group(1)
    nav = one(r'<nav class="toc"[^>]*>.*?(<ol>.*</ol>)\s*</nav>', page, "the contents").group(1)
    meta = text_of(one(r'<div class="meta">(.*?)</div>', page, "the version line").group(1))
    version = one(r"VERSION\s+([\d.]+)", meta, "the version number").group(1)
    month = meta.split("·")[-1].strip()
    logo_white = one(r'<footer class="end"><img src="([^"]+)"', page, "the white logo").group(1)
    body = one(r'(<section class="chapter".*?)<footer class="end">', page, "the chapters").group(1)
    cover_shot = one(r'<img class="im-light"[^>]*?src="([^"]+)"[^>]*?alt="After Enhance:', body,
                     "the After Enhance picture").group(1)

    # pictures: the Day version only, no zoom buttons, no lazy loading
    body = re.sub(r'<img class="im-dark"[^>]*>', "", body)
    body = re.sub(r'<button class="zoom"[^>]*>(.*?)</button>', r'<span class="zoom">\1</span>', body, flags=re.S)
    body = body.replace(' loading="lazy"', "")
    body = footnotes_inline(body)

    # chapter names for the running header and the PDF bookmarks
    def chapter(m):
        title = text_of(re.sub(r'<span class="num">.*?</span>', "", m.group(2)))
        return f'{m.group(1)} data-title="{html.escape(title)}">\n<h1 data-title="{html.escape(title)}">{m.group(2)}</h1>'
    body = re.sub(r'(<section class="chapter" id="[^"]+")>\s*<h1>(.*?)</h1>', chapter, body, flags=re.S)

    def heading2(m):
        inner = re.sub(r'<span class="step">(\d+)</span>', r"\1. ", m.group(2))
        return f'<h2{m.group(1)} data-title="{html.escape(text_of(inner))}">{m.group(2)}</h2>'
    body = re.sub(r"<h2([^>]*)>(.*?)</h2>", heading2, body, flags=re.S)

    # contents with page numbers
    rows = []
    for m in re.finditer(r'<li><a href="#([^"]+)"><span class="n">(\d+)</span><span>(.*?)</span></a>(<ol>.*?</ol>)?',
                         nav, re.S):
        anchor, num, title, sub = m.groups()
        rows.append(f'<div class="toc-row"><span class="n">{num}</span><a href="#{anchor}">{title}</a></div>')
        if sub:
            items = "".join(f'<li><a href="#{a}">{t}</a></li>'
                            for a, t in re.findall(r'<a href="#([^"]+)">(.*?)</a>', sub, re.S))
            rows.append(f'<ol class="toc-sub">{items}</ol>')
    contents = "\n".join(rows)

    return f"""<!doctype html>
<html lang="en" data-theme="light">
<head><meta charset="utf-8"><title>VoiceCleaner User Manual</title>
<meta name="author" content="Steamburger Studios">
<meta name="description" content="User manual for VoiceCleaner {version}">
<style>{style}</style>
<style>{PRINT_CSS}</style>
</head>
<body>
<section class="cover">
  <div class="cover-top">
    <div class="cover-brand">
      <img class="cover-logo" src="{logo_white}" alt="">
      <div><div class="cover-kicker">STEAMBURGER STUDIOS</div><div class="cover-name">VOICECLEANER</div></div>
    </div>
    <div class="cover-wave">{waveform_svg()}</div>
  </div>
  <div class="cover-bottom">
    <div class="cover-title">USER MANUAL</div>
    <p class="cover-sub">Clean, clear speech from noisy recordings: free, offline, for Windows and Mac.
    Quick start, every tab and setting, keyboard shortcuts and troubleshooting.</p>
    <img class="cover-shot" src="{cover_shot}" alt="VoiceCleaner after enhancing a recording">
    <div class="cover-meta"><span>VERSION {version}</span><span>{month.upper()}</span><span>STEAMBURGERSTUDIOS.COM</span></div>
  </div>
</section>
<section class="contents" data-title="Contents">
<h1 class="contents-title">Contents</h1>
{contents}
</section>
{body}
</body>
</html>"""


PRINT_CSS = """
@page {
  size: Letter; margin: 20mm 17mm 19mm; background: #f2f2f5;
  @top-left { content: "VOICECLEANER · USER MANUAL"; font: 500 7pt 'DM Mono', monospace; letter-spacing: 2.4pt; color: #6e6e8a; }
  @top-right { content: string(chap); font: 500 7pt 'DM Mono', monospace; letter-spacing: 2.4pt; color: #6e6e8a; text-transform: uppercase; }
  @bottom-left { content: "Steamburger Studios"; font: 7.5pt 'DM Mono', monospace; letter-spacing: 1.5pt; color: #6e6e8a; }
  @bottom-right { content: counter(page); font: 500 8pt 'DM Mono', monospace; color: #2563eb; }
  @footnote { border-top: .6pt solid #c3c3cc; padding-top: 5pt; margin-top: 10pt; }
}
@page cover { margin: 0; background: #f2f2f5;
  @top-left { content: none; } @top-right { content: none; } @bottom-left { content: none; } @bottom-right { content: none; } }
html { font-size: 9.6pt; }
body { background: transparent; font-size: 9.6pt; line-height: 1.62; }
a { color: #2563eb; }

/* cover */
.cover { page: cover; break-after: page; height: 279.4mm; }
.cover-top { background: #07070e; height: 104mm; padding: 22mm 18mm 0; }
.cover-brand { display: flex; align-items: center; gap: 9mm; }
.cover-logo { width: 34mm; height: 34mm; }
.cover-kicker { font: 500 9pt 'DM Mono', monospace; letter-spacing: 5pt; color: #60a5fa; }
.cover-name { font-family: 'Bebas Neue', sans-serif; font-size: 66pt; line-height: .95; letter-spacing: 1.5pt; color: #eeeef8; }
.cover-wave { margin-top: 9mm; height: 28mm; }
.cover-wave svg { width: 100%; height: 28mm; }
.cover-bottom { padding: 15mm 18mm 0; }
.cover-title { font-family: 'Bebas Neue', sans-serif; font-size: 40pt; letter-spacing: 1pt; color: #0a0a12; line-height: 1; }
.cover-sub { font-size: 11pt; line-height: 1.55; color: #363645; margin: 5mm 0 7mm; max-width: 150mm; }
.cover-shot { display: block; width: 150mm; border-radius: 6px; border: 1px solid #c3c3cc; }
.cover-meta { display: flex; justify-content: space-between; margin-top: 9mm; font: 500 8pt 'DM Mono', monospace; letter-spacing: 3pt; color: #6e6e8a; }

/* contents */
.contents { string-set: chap "Contents"; break-after: page; }
.contents-title { font-family: 'Bebas Neue', sans-serif; font-weight: 400; font-size: 38pt; margin: 0 0 7mm; color: #0a0a12; }
.toc-row { display: flex; align-items: baseline; border-bottom: .6pt solid #dcdce3; padding: 4.2pt 0; }
.toc-row .n { font: 500 7.5pt 'DM Mono', monospace; color: #3b82f6; width: 11mm; }
.toc-row a { flex: 1; color: #0a0a12; font-weight: 700; font-size: 10.5pt; }
.toc-row a::after, .toc-sub a::after { content: target-counter(attr(href), page); float: right; font: 8pt 'DM Mono', monospace; color: #6e6e8a; }
.toc-sub { columns: 2; column-gap: 12mm; list-style: none; margin: 3pt 0 4pt 11mm; padding: 0; }
.toc-sub li { margin: 0; font-size: 8pt; line-height: 1.6; }
.toc-sub a { color: #363645; display: block; }

/* chapters */
section.chapter { break-before: page; string-set: chap attr(data-title); padding: 0; border: none; max-width: none; }
section.chapter h1 { font-size: 40pt; margin: 0 0 8pt; bookmark-level: 1; bookmark-label: attr(data-title); }
section.chapter h1 .num { font-size: 7.5pt; margin-bottom: 7pt; }
section.chapter .lead { font-size: 11pt; }
section.chapter h2 { font-size: 13.5pt; margin: 16pt 0 5pt; bookmark-level: 2; bookmark-label: attr(data-title); break-after: avoid; }
section.chapter h3 { font-size: 7.5pt; margin: 14pt 0 5pt; bookmark-level: none; break-after: avoid; }
.contents-title { bookmark-level: 1; bookmark-label: "Contents"; }
p, li, dd { orphans: 2; widows: 2; }
figure.shot { margin: 8pt 0 12pt; break-inside: avoid; }
figure.shot img { box-shadow: none; border-radius: 6px; }
figure.shot .zoom { display: block; }
figcaption { font-size: 7.8pt; }
figure.small { max-width: 120mm; } figure.menu { max-width: 70mm; } figure.side { max-width: 68mm; }
table { font-size: 8.6pt; border-radius: 0; }
tr, .callout, .cheat, pre, dl dt { break-inside: avoid; }
dl dt { break-after: avoid; margin-top: .5em; }
dl dd { line-height: 1.5; }
th { font-size: 6.8pt; padding: 6pt 9pt; }
td { padding: 6pt 9pt; }
pre { font-size: 7.6pt; white-space: pre-wrap; }
.callout, .cheat { padding: 9pt 12pt; }
.two-col { gap: 8mm; }

/* notes at the foot of the page */
.pfn { float: footnote; font: 7.4pt/1.45 'Syne', sans-serif; color: #6e6e8a; text-align: left; }
.pfn code, .pfn kbd, .pfn .ui { font-size: .9em; }
::footnote-call { content: counter(footnote); font: 500 6.2pt 'DM Mono', monospace; vertical-align: super; line-height: 0; color: #2563eb; padding-left: 1pt; }
::footnote-marker { content: counter(footnote) ".  "; font: 500 6.8pt 'DM Mono', monospace; color: #2563eb; }
"""


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    from weasyprint import HTML  # noqa: PLC0415 - only this tool needs it

    doc = HTML(string=build(src), base_url=str(src.parent)).render()
    doc.write_pdf(out)
    print(f"{out}: {len(doc.pages)} pages, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
