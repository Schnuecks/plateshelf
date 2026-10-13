"""Mirroring the docs into the wiki (tools/wiki_sync.py)."""
import re
from pathlib import Path

from wiki_sync import PAGES, sync

DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_every_doc_becomes_a_page_with_working_links(tmp_path):
    (tmp_path / "Alt.md").write_text("veraltet")              # deleted pages disappear
    written = sync(DOCS, tmp_path, "v9.9.9", "Schnuecks/plateshelf")
    assert len(written) == sum(len(p) for p in PAGES.values()) and not (tmp_path / "Alt.md").exists()
    pages = {p.stem for p in tmp_path.glob("*.md")}
    for page in pages - {"_Sidebar", "_Footer", "Home"}:
        text = (tmp_path / f"{page}.md").read_text(encoding="utf-8")
        assert text.startswith("> ") and "v9.9.9" in text.splitlines()[0]   # notice „wird gespiegelt“
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if target.startswith(("http", "#")):
                continue
            assert target.split("#")[0] in pages, (page, target)        # only wiki pages, no .md paths
    usage = (tmp_path / "Usage.md").read_text(encoding="utf-8")
    assert "](DE-Bedienung)" in usage and "](Documentation)" in usage
    assert "https://github.com/Schnuecks/plateshelf/blob/v9.9.9/README.md" in (tmp_path / "Documentation.md").read_text(
        encoding="utf-8")
    sidebar = (tmp_path / "_Sidebar.md").read_text(encoding="utf-8")
    assert "**English**" in sidebar and "**Deutsch**" in sidebar and "(DE-Konfiguration)" in sidebar
