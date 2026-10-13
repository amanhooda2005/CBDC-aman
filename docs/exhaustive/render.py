"""Renders docs/EXHAUSTIVE_REPORT.md to a self-contained HTML (images embedded, table of contents).
Usage: python docs/exhaustive/render.py [out.html]   (then print to PDF with any browser)."""
import base64
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt

DOCS = Path(__file__).resolve().parents[1]
src = (DOCS / "EXHAUSTIVE_REPORT.md").read_text(encoding="utf-8")
body = MarkdownIt("commonmark").enable("table").render(src)


def embed(m):
    path = (DOCS / m.group(1)).resolve()
    return f'src="data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}"'


body = re.sub(r'src="([^"]+\.png)"', embed, body)
toc, n = [], [0]


def anchor(m):
    n[0] += 1
    level, text = m.group(1), m.group(2)
    plain = re.sub("<[^>]+>", "", text)
    if level == "1" or (level == "2" and not plain.startswith(("A.", "B.", "C."))):
        toc.append((level, n[0], plain))
    return f'<h{level} id="s{n[0]}">{text}</h{level}>'


body = re.sub(r"<h([12])>(.*?)</h\1>", anchor, body)
toc_html = "<nav><h2>Contents</h2>" + "".join(
    f'<div class="toc{l}"><a href="#s{i}">{t}</a></div>' for l, i, t in toc) + "</nav>"
# contents go after the front matter, just before Part 1 (the second top-level heading)
second_h1 = [m.start() for m in re.finditer('<h1 id="s', body)][1]
body = body[:second_h1] + toc_html + body[second_h1:]
css = """body{font-family:Georgia,serif;font-size:10pt;line-height:1.42;max-width:900px;margin:auto;color:#111}
h1{font-size:18pt;margin-top:28px;page-break-before:always;border-bottom:2px solid #333}h1:first-of-type{page-break-before:avoid}
h2{font-size:13pt;border-bottom:1px solid #aaa;margin-top:20px}h3{font-size:11pt}h4,h5,h6{font-size:10pt}
table{border-collapse:collapse;margin:8px 0;font-size:7.6pt;width:100%}th,td{border:1px solid #bbb;padding:2px 4px;text-align:left;vertical-align:top;word-break:break-word}
th{background:#eee}tr{page-break-inside:avoid}code{font-family:Consolas,monospace;font-size:8pt;background:#f3f3f3}
pre{background:#f6f6f6;padding:6px;font-size:7.4pt;white-space:pre-wrap;word-break:break-word}img{max-width:100%;page-break-inside:avoid}
nav{page-break-before:always}nav .toc1{font-weight:bold;margin-top:6px}nav .toc2{margin-left:18px;font-size:9pt}nav a{color:#123;text-decoration:none}
@page{size:A4;margin:14mm}"""
out = Path(sys.argv[1]) if len(sys.argv) > 1 else DOCS / "EXHAUSTIVE_REPORT.html"
out.write_text(f'<!doctype html><html><head><meta charset="utf-8"><title>CBDC Exhaustive Experiment Record</title><style>{css}</style></head><body>{body}</body></html>', encoding="utf-8")
print(out, f"{out.stat().st_size:,} bytes", len(toc), "toc entries", body.count("<table>"), "tables", body.count("<img"), "images")
