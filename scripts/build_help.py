#!/usr/bin/env python3
"""Convert README.md → data/help.html.

Run this whenever README.md changes:
    python scripts/build_help.py

The generated file is committed to the repo and installed by the Flatpak build.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import markdown
    from markdown.extensions.tables import TableExtension
    from markdown.extensions.fenced_code import FencedCodeExtension
except ImportError:
    sys.exit("markdown library not found — run: pip install markdown")

ROOT   = Path(__file__).resolve().parent.parent
SRC_MD = ROOT / "data" / "docs" / "user-guide.md"
OUT    = ROOT / "data" / "help.html"

CSS = """
:root { color-scheme: light dark; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 14px;
    line-height: 1.7;
    max-width: 820px;
    margin: 0 auto;
    padding: 24px 28px 64px;
    color: #1a1a1a;
    background: #ffffff;
}
@media (prefers-color-scheme: dark) {
    body { color: #e0e0e0; background: #1c1c1c; }
    pre  { background: #252525; }
    code { background: #2d2d2d; color: #e0e0e0; }
    pre code { background: none; }
    table th { background: #252525; }
    table td, table th { border-color: #3a3a3a; }
    hr { border-color: #3a3a3a; }
    h1 { border-bottom-color: #3a3a3a; }
    h2 { border-bottom-color: #2e2e2e; }
    a  { color: #7eb8f7; }
    blockquote { border-left-color: #404040; color: #aaa; }
}
h1, h2, h3 { font-weight: 600; margin-top: 1.6em; margin-bottom: 0.4em; }
h1 { font-size: 1.8em; border-bottom: 2px solid #e0e0e0; padding-bottom: 0.3em; margin-top: 0.5em; }
h2 { font-size: 1.25em; border-bottom: 1px solid #e8e8e8; padding-bottom: 0.2em; }
h3 { font-size: 1.05em; }
h4, h5, h6 { font-size: 1em; font-weight: 600; }
pre {
    background: #f5f5f5;
    border-radius: 6px;
    padding: 12px 16px;
    overflow-x: auto;
    font-size: 13px;
    line-height: 1.5;
}
code {
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
    background: #f0f0f0;
    padding: 2px 5px;
    border-radius: 3px;
    font-size: 0.88em;
}
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 0.9em 0 1.1em; }
table th, table td { border: 1px solid #ddd; padding: 7px 12px; text-align: left; }
table th { background: #f5f5f5; font-weight: 600; }
ul, ol { padding-left: 1.5em; }
li { margin-bottom: 0.3em; }
hr { border: none; border-top: 1px solid #e0e0e0; margin: 1.6em 0; }
a { color: #1a73e8; text-decoration: none; }
a:hover { text-decoration: underline; }
p { margin: 0.4em 0 0.8em; }
blockquote { border-left: 3px solid #ccc; margin: 0.8em 0; padding: 0.2em 1em; color: #666; }
"""

TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width">
  <title>CloudSync Help</title>
  <style>
{css}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def build() -> None:
    if not SRC_MD.exists():
        sys.exit(f"Source not found: {SRC_MD}")

    src = SRC_MD.read_text(encoding="utf-8")
    body = markdown.markdown(
        src,
        extensions=[
            TableExtension(),
            FencedCodeExtension(),
            "toc",
            "nl2br",
        ],
    )
    html = TEMPLATE.format(css=CSS, body=body)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"Written: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
