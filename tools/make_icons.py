# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Creates the app icons (PNG) from app/static/logo.svg, for the home screen and web app manifest.

    python tools/make_icons.py

Requires Playwright (requirements-browser.txt). Creates in app/static/icons/:
- icon-192.png, icon-512.png: the logo as is (rounded square)
- icon-maskable-512.png: full-bleed, logo scaled down to 80 % so Android can crop it round
  or square without cutting anything off
- apple-touch-icon.png (180 px): full-bleed; iOS rounds the corners itself
"""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "app" / "static" / "logo.svg"
OUT = ROOT / "app" / "static" / "icons"
BACKGROUND = "#0B6E63"  # base color of the logo


def _full_bleed(svg: str, scale: float) -> str:
    """Full-bleed square in the logo color, content centered and scaled down to `scale`."""
    inner = re.sub(r'<rect width="64" height="64" rx="14"[^>]*/>', "", svg)
    body = inner[inner.index(">") + 1:inner.rindex("</svg>")]
    offset = 32 * (1 - scale)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            f'<rect width="64" height="64" fill="{BACKGROUND}"/>'
            f'<g transform="translate({offset} {offset}) scale({scale})">{body}</g></svg>')


def main() -> None:
    svg = LOGO.read_text(encoding="utf-8")
    OUT.mkdir(exist_ok=True)
    jobs = [("icon-192.png", svg, 192), ("icon-512.png", svg, 512),
            ("icon-maskable-512.png", _full_bleed(svg, 0.8), 512),
            ("apple-touch-icon.png", _full_bleed(svg, 0.86), 180)]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, source, size in jobs:
            page = browser.new_page(viewport={"width": size, "height": size})
            page.set_content(f'<style>html,body{{margin:0;background:transparent}}svg{{display:block;width:{size}px;'
                             f'height:{size}px}}</style>{source}')
            page.screenshot(path=OUT / name, omit_background=True)
            page.close()
            print(f"{OUT / name}  {size} px")
        browser.close()


if __name__ == "__main__":
    main()
