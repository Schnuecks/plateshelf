# Development

**English** · [Deutsch](../de/development.md) · [← Documentation](README.md)

## Running and testing locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m ruff check                       # linter, also runs in CI
python -m pytest -q                        # run the tests
python tools/make_sample.py ./library      # create sample files
LIBRARY_DIR=./library DATA_DIR=./data uvicorn app.main:app --reload
```

There is also a browser test that opens all pages and the 3D preview in Chromium. It
fails on any JavaScript error, on any request to another server, and on serious or
critical accessibility problems (WCAG 2 A/AA, checked with axe-core from `tests/vendor/`,
in the light and the dark theme):

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
python -m pytest -q tests/test_browser.py
```

`python tools/make_screenshots.py` recreates the screenshots in the README and docs.
`python tools/load_test.py 2000` builds a library with 2000 files and times reading and
the main pages. `python tools/make_icons.py` renders the home screen icons from
`app/static/logo.svg`.

## Translations

The German texts in the code double as keys for the translations in
`app/locales/<language>.json`. After changing or adding texts,

```bash
python tools/i18n_extract.py
```

shows per language what is missing or no longer used. The tests fail as long as a
translation is missing or its placeholders (`{n}`, `{name}` …) don’t match the German
text. A new language needs an entry in `LANGUAGES`, `NUMBER_FORMAT`, `MONTHS` and
`DAY_MONTH` in `app/i18n.py` plus its own JSON file.

## Reporting problems

Bug reports and ideas are welcome as GitHub issues; the forms ask for the details that
are usually needed. For bugs, open “Settings” → “Diagnostics” in PlateShelf, click “Copy”
and paste the text into the issue. It lists version, platform, library figures, printer
types with their last result, which settings are set and the latest warnings, without
credentials, IP addresses, host names or printer names.

## Tests, builds and releases

- Every pull request and every push to `main` runs Ruff, the Python tests and the browser
  test and builds the image for amd64 and arm64 as a trial. Nothing is
  published.
- Trivy then scans the image for known vulnerabilities. Critical and high ones that
  already have a fix are listed; the build only fails on critical ones, so a fresh “high”
  in the base image does not block a release until Debian has shipped the fix.
- A release is created by a version tag:

  1. Add the changes to `CHANGELOG.md` under the new version number.
  2. Create and push the tag:

     ```bash
     git tag -a v1.2.3 -m "PlateShelf 1.2.3" && git push --tags
     ```

  3. GitHub Actions tests, builds the image for all platforms and publishes it as
     `1.2.3`, `1.2` and `latest`. Older versions are then removed; the last five releases
     are kept.

- Dependabot (`.github/dependabot.yml`) checks Python packages, GitHub Actions and the
  base image weekly and proposes updates as pull requests. Known vulnerabilities are
  reported immediately and fixed automatically via pull request. Such updates reach the
  published image with the next release.
