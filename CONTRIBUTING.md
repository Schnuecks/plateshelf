# Contributing to PlateShelf

Thanks for your interest! Bug reports, ideas and pull requests are welcome.

## Reporting problems

Use the issue forms: **Bug report**, **Printer or slicer support** or **Feature request**.
For bugs, please paste the text from **Settings → Diagnostics** in PlateShelf; it contains
no credentials, IP addresses or printer names. German is fine too.

Security problems go to **Security → Report a vulnerability** instead, see
[SECURITY.md](SECURITY.md).

## Pull requests

1. Open an issue first for larger changes, so we can agree on the approach.
2. Set up the development environment as described in
   [Development](docs/en/development.md) and make sure `python -m pytest -q` passes.
3. Keep PlateShelf simple: it is an archive for sliced files, not a printer control panel.
4. Texts in the interface are written in German and translated in `app/locales/`
   (English, French, Spanish, Italian, Dutch). `python tools/i18n_extract.py` lists missing
   translations; please add all five or say which ones you could not do.
5. Update the documentation in `docs/en` and `docs/de` (and the README if needed) and add a line under “Unreleased” in
   `CHANGELOG.md` if users will notice the change.

The pull request template has a short checklist for this.

## Code layout

| Path | Contents |
|---|---|
| `app/main.py` | App, background tasks and the checks in front of every page |
| `app/routes_*.py` | Pages and API endpoints |
| `app/web.py`, `app/projects.py` | Templates and formatting, preparing projects for the pages |
| `app/parser.py`, `app/gcode_file.py`, `app/bgcode.py` | Reading `.gcode.3mf`, `.gcode` and `.bgcode` |
| `app/gcode_preview.py`, `app/static/viewer.js` | 3D preview |
| `app/printer_import.py`, `app/print_history.py` | Direct import and print history |
| `tests/` | Tests; `tests/test_browser.py` needs Playwright |

## License

By contributing you agree that your contribution is licensed under the
[AGPL-3.0-or-later](LICENSE), like the rest of PlateShelf.
