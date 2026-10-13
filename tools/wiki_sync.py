# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Mirrors docs/en and docs/de into the GitHub wiki.

The source remains docs/ in the repo (versioned, reviewed via PR); the wiki is just a view
with a sidebar. Every run rewrites all wiki pages, so changes made directly
in the wiki are lost – every page points this out at the top.

    python tools/wiki_sync.py docs <wiki-checkout> --ref v1.0.0 --repo Schnuecks/plateshelf
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# File in docs/<lang>/ -> name of the wiki page (English without prefix, German with "DE-")
PAGES = {
    "en": {"README": "Documentation", "installation": "Installation", "configuration": "Configuration",
           "usage": "Usage", "printers": "Printers", "dashboard": "Dashboard", "development": "Development"},
    "de": {"README": "Dokumentation", "installation": "DE-Installation", "configuration": "DE-Konfiguration",
           "usage": "DE-Bedienung", "printers": "DE-Drucker", "dashboard": "DE-Dashboard",
           "development": "DE-Entwicklung"},
}
NOTICE = {
    "en": "> This page is mirrored automatically from [`docs/en/{file}`]({src}) ({ref}). "
          "Changes made here are overwritten; please suggest them as a pull request.",
    "de": "> Diese Seite wird automatisch aus [`docs/de/{file}`]({src}) gespiegelt ({ref}). "
          "Änderungen hier werden überschrieben; bitte als Pull Request vorschlagen.",
}
_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _rewrite(target: str, lang: str, ref: str, repo: str) -> str:
    """Rewrite a relative link from docs/<lang>/ to a wiki page or a file in the repo."""
    if re.match(r"^[a-z]+:|^#", target):
        return target  # external, or an anchor on the same page
    path, _, anchor = target.partition("#")
    anchor = f"#{anchor}" if anchor else ""
    m = re.fullmatch(r"(?:\.\./(en|de)/)?([\w-]+)\.md", path)
    if m and m[2] in PAGES[m[1] or lang]:
        return PAGES[m[1] or lang][m[2]] + anchor
    resolved = (Path("docs") / lang / path).as_posix()
    parts: list[str] = []
    for part in resolved.split("/"):
        if part == "..":
            parts.pop()
        elif part != ".":
            parts.append(part)
    kind = "raw" if re.search(r"\.(png|jpe?g|gif|svg|webp)$", path, re.I) else "blob"
    return f"https://github.com/{repo}/{kind}/{ref}/{'/'.join(parts)}{anchor}"


def convert(text: str, lang: str, file: str, ref: str, repo: str) -> str:
    body = _LINK.sub(lambda m: "](" + _rewrite(m[1], lang, ref, repo) + ")", text)
    src = f"https://github.com/{repo}/blob/{ref}/docs/{lang}/{file}"
    return NOTICE[lang].format(file=file, src=src, ref=ref) + "\n\n" + body


def sidebar(docs: Path) -> str:
    out = []
    for lang, title in (("en", "**English**"), ("de", "**Deutsch**")):
        out.append(title)
        out.append("")
        for stem, page in PAGES[lang].items():
            if (docs / lang / f"{stem}.md").exists():
                heading = (docs / lang / f"{stem}.md").read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
                out.append(f"- [{heading}]({page})")
        out.append("")
    return "\n".join(out)


def home(repo: str) -> str:
    return (f"# PlateShelf\n\nSelf-hosted archive for your 3D prints. · Selbst gehostetes Archiv für deine 3D-Drucke.\n\n"
            f"- **English:** [Documentation](Documentation)\n- **Deutsch:** [Dokumentation](Dokumentation)\n\n"
            f"Source code, issues and releases: https://github.com/{repo}\n")


def sync(docs: Path, wiki: Path, ref: str, repo: str) -> list[str]:
    for old in wiki.glob("*.md"):
        old.unlink()  # start fresh: renamed or deleted pages disappear too
    written = []
    for lang, pages in PAGES.items():
        for stem, page in pages.items():
            src = docs / lang / f"{stem}.md"
            if src.exists():
                (wiki / f"{page}.md").write_text(convert(src.read_text(encoding="utf-8"), lang, src.name, ref, repo),
                                                 encoding="utf-8")
                written.append(page)
    (wiki / "Home.md").write_text(home(repo), encoding="utf-8")
    (wiki / "_Sidebar.md").write_text(sidebar(docs), encoding="utf-8")
    (wiki / "_Footer.md").write_text(f"[PlateShelf on GitHub](https://github.com/{repo}) · {ref}\n", encoding="utf-8")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("docs", type=Path)
    ap.add_argument("wiki", type=Path)
    ap.add_argument("--ref", default="main")
    ap.add_argument("--repo", default="Schnuecks/plateshelf")
    args = ap.parse_args()
    pages = sync(args.docs, args.wiki, args.ref, args.repo)
    print(f"{len(pages)} Seiten nach {args.wiki} geschrieben")


if __name__ == "__main__":
    main()
