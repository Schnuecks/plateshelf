# Changelog

## 1.2.1

- **Plates sliced again count once:** print time, filament, costs and the number of plates of a
  project counted every version of a plate that was sliced in several files. Now each plate counts
  once, with its latest slice; "printed" counts a plate if any of its versions was printed.
- **Plate bar:** versions of a plate that are practically the same slice (same layer height and
  filaments, time and weight within 5 %) appear only once, preferably the printed one.
- **Prints are matched right after an upload:** a scan that finds new files now links prints without
  a file at once, instead of at the next printer sync (up to 10 minutes).

## 1.2.0

- **Prints without a file from the printer** (e.g. the P2S kept the file sent from Bambu Studio
  in its internal storage, out of reach for PlateShelf) are matched to an uploaded file with the
  same project name and plate, from half an hour after the start. The docs explain the setup.

## 1.1.1

- **Bonbon in dark:** the highlighted numbers in the summary above the projects were hard to read,
  because the yellow marker only covered their lower half. They are now full yellow labels.
- **Language and style on iPhone:** in the expanded menu on phones, Safari closed the menu instead of
  opening the language or style list. On phones both are now select fields that apply right away.

## 1.1.0

- **Four new styles:** besides the classic look, PlateShelf now comes as Candy, Workbench,
  Blueprint and Pinboard, each in light and dark. Admins set the default under Settings → Style;
  everyone can pick their own in the palette menu of the title bar.
- Plates that are only included as a preview are greyed out in the image only; their names stay
  readable (contrast).

## 1.0.2

- **Image without pip:** pip is removed after the build. The copy from the Python base image
  bundled an outdated certifi with compromised root certificates (GHSA-6q48-6c7w-9gw2, critical),
  which PlateShelf never used.
- **3D preview showed a third colour for Bambu Lab P2S prints:** the current P2S start G-code
  selects the first filament in an indented block, which the preview skipped. Everything up to the
  first filament change was drawn in no filament's colour. Previews and preview images are
  recalculated once.
- **Print history matched a P2S print to the previous project:** when the printer reports only the
  process profile as name ("0.2mm layer, 2 walls, 15% infill"), a new print could end up at a plate
  of a project fetched a day earlier, and this was never corrected. A plate now only counts if it
  was fetched within the hour before the start; such matches from the last 30 days are corrected
  after the next import.

## 1.0.1

- **Language and theme switch could redirect to another site:** its return address was checked
  without the backslash rule of the sign-in, so `/\evil.example` became `//evil.example` in the
  browser. It now uses the same check (found by CodeQL code scanning).
- File names of backups and photos are matched in full (`fullmatch`), not only up to a line break.

## 1.0.0

First stable release. PlateShelf is a self-hosted archive for your 3D prints:

- **Library:** sliced files from Bambu Studio, OrcaSlicer, PrusaSlicer, Cura and others
  (`.gcode.3mf`, `.gcode`, `.bgcode`), grouped into projects, with plates, preview images, print
  time, filament, costs, tags, notes and your own photos of the result.
- **3D preview** of the actual toolpaths with playback, colouring by filament, line type, speed,
  flow, fan or temperature, also on phones.
- **Direct import** from Bambu Lab, Prusa (PrusaLink), Klipper (Moonraker) and
  OctoPrint printers, with print history, live progress of running prints and statistics.
- **Accounts** with roles, passkeys, single sign-on (OIDC), API tokens for dashboards, daily
  backups, six languages, light and dark theme, installable as an app on phones.
- **Security:** reviewed before this release (versions 0.17 and 0.18): strict Content Security
  Policy, limits on failed sign-ins, protection against oversized files, container without root,
  Trivy and OWASP ZAP scans on every change.
- One Docker container for amd64 and arm64; the image is now also tagged `:1`.

Coming from a 0.x version: no manual steps, `docker compose pull && docker compose up -d`.

## 0.18.0

- **Stricter Content Security Policy:** no inline styles any more (`style-src 'self'`); colours
  and bars are set by the page script. Forms may only go to PlateShelf itself, the OIDC provider
  and, new, the sign-in page in `AUTH_PORTAL_URL` (before: any `https:` address). **Behind a
  reverse proxy with a login page (Authelia, Authentik …), set `AUTH_PORTAL_URL`**, otherwise a
  form sent after the proxy's session ran out is blocked instead of leading to its login page.

## 0.17.3

Hardening after the ZAP baseline scan (it found no actual vulnerability):

- New headers `Cross-Origin-Resource-Policy: same-origin` and `Cross-Origin-Embedder-Policy:
  require-corp`: other sites cannot embed PlateShelf's files, and its pages only load what comes
  from PlateShelf itself.
- The language and theme cookies are `HttpOnly`; no script needs to read them.
- `.zap/rules.tsv`: known false alarms of the scan are documented and ignored.

## 0.17.2

- The image runs as an unprivileged user (UID 10001) instead of root. With the supplied
  `compose.yaml` nothing changes, `user: PUID:PGID` takes precedence. Without a `user:` line,
  the library and data folders must be writable for UID 10001, or set `user:`.

## 0.17.1

- `compose.yaml` sets `FORWARDED_ALLOW_IPS=127.0.0.1`, as it publishes the port directly: devices in
  the local network can no longer put a made-up address into the sign-in log. **Behind a reverse
  proxy, set its address in `.env`** (see the installation guide). The image itself still trusts
  local and private networks.

## 0.17.0

Security review before the public release:

- **Sign-in lock could be bypassed:** the container trusted `X-Forwarded-For` from anywhere, and
  uvicorn then took the first address, which the client chooses itself. With a new made-up address
  per attempt, passwords could be guessed without limit. Now only proxies in `FORWARDED_ALLOW_IPS`
  are trusted (default: local and private networks), and the address the proxy added is used. In
  addition, a user name is locked after 20 failed attempts within 15 minutes from any number of
  addresses (addresses the account signed in from before are exempt), and HTTP Basic Auth now has
  the same lock as the sign-in form.
- **Zip bombs in 3MF and .bgcode files:** settings, model data and images were read without a size
  limit; a 300 KB file could take gigabytes of memory, again on every start. Now each part is capped
  (32 MB for settings and model data, 10 MB per image, 256 MB per file) and such files show up as
  read errors. The designer data is read as a stream up to the mesh, so 3MF files with a large
  inline mesh still work. `.bgcode` blocks are capped at their stated size (at most 16 MB), the 3D
  preview refuses plate G-code over 4 GB.
- **Single sign-on no longer takes over accounts by name:** before, the first OIDC sign-in linked a
  local account with the same name, falling back to the display name, which many providers let
  people choose freely, admins included. Now an existing account is linked only by its owner:
  sign in as usual, then "My account" → "Link with …".
- Downloads from printers stop when a file grows beyond the size the printer listed.
- Warning in the log at start when printer credentials are stored unencrypted (no `SECRET_KEY`).
- Base image pinned to its digest, GitHub Actions to commit SHAs (Dependabot keeps both current).

## 0.16.3

- **Python 3.14:** the Docker image is now built on `python:3.14-slim` (before 3.12), and CI tests
  with 3.14. Python 3.15 follows once all dependencies have ready-made packages for it.

## 0.16.2

- **Home-screen app on iOS stays up to date:** when you come back to the app after more than a
  minute (or iOS shows the page from its cache), the page reloads; after a short pause the running
  prints and the library are fetched right away instead of on the next timer. Pages where something
  may be being edited (settings, upload, account) are not reloaded. Pages and API answers are also
  sent with `Cache-Control: no-cache`, so no old state is shown from a cache.

## 0.16.1

- **Menu on phones:** the menu now opens as a panel above the page (the page behind is dimmed, a tap
  next to it closes it) instead of pushing the page down with a narrow column. Every entry has an
  icon and a full-width row; language and theme are labelled and show the current choice. On
  larger screens the menu entries got the same icons.
- **Diagnostics text without scrolling:** the field grows with the text.

## 0.16.0

- **Diagnostics as a file:** "Settings" → "Diagnostics" → "Download as file" creates a ZIP for bug
  reports: the diagnostics text, the last 500 log lines and, for each printer, the result of the
  last sync, the file list on the printer and the last raw answer to the status query (Bambu MQTT,
  Moonraker, PrusaLink, OctoPrint). Credentials, serial numbers, addresses and printer names are
  removed; a short list in the settings explains what is collected and why. The answers are only
  kept in memory, nothing is sent.

## 0.15.5

- **Printer names from the settings:** files fetched from a printer now show the name you gave that
  printer in the settings (e.g. "Prusa Mini") instead of the slicer profile ("Original Prusa MINI &
  MINI+ Input Shaper"), in the printer filter, on the project page, in the statistics and for the
  printer power. Uploaded files keep the profile name.

## 0.15.4

- **Running prints count as the newest:** sorted by newest or last printed, projects that are
  printing right now are at the top of the project list (the one finishing next first). Before, a
  project sliced weeks ago could be far down the list while it was printing.

## 0.15.3

- **No duplicates from the Bambu cache folder:** the P2S keeps the same file in the main folder and
  in `cache`; 0.15.2 imported both, so prints showed up twice. Copies with identical content are now
  only linked to the existing file, and duplicates already imported are removed once (their prints
  move to the original).
- **More room in the top bar:** History, trash, settings, account and sign-out moved into a menu
  (☰) on the right; upload, language and theme stay visible. The search shows only the field and a
  "Filter" button; the filters open as a dropdown, so the top bar stays on one row.
- **Newest files first:** when a printer has many files to fetch (e.g. the first import of the cache
  folder), the newest come first, so the running print is linked to its project quickly.

## 0.15.2

- **Bambu P1 and A1 series:** prints sent from Bambu Studio or the cloud are stored by these
  printers in the `cache` folder of the SD card, not in the main folder. PlateShelf now also fetches
  the sliced projects from there (`.3mf`, saved as `.gcode.3mf`), so their prints are linked to a
  project. Unsliced files and the extracted G-code copies next to them are skipped.

## 0.15.1

- **Finished prints shown as finished right away:** on the project page and in the history, a
  running print that ends now switches to “Finished” by itself (the page reloads once) instead of
  staying at its last percentage. Finished prints are always stored with 100 %, even when the
  printer (e.g. Klipper) no longer reports progress at the end.

## 0.15.0

- **Several prints at once:** the top bar now shows the print that finishes next (not the one
  started first), and “+2” opens a list of all running prints with links. The browser tab reads
  e.g. “3 prints · next 87 %”, and a project printing on several printers shows “Printing 2× 80 % /
  20 %” on its card.
- **Printers queried in parallel:** fetching files and print status now runs for all printers at the
  same time, so a printer that is switched off no longer delays the others.

## 0.14.3

- **Clean thumbnails for large prints:** PlateShelf’s own thumbnails no longer show the inside of
  big models through gaps in the walls. Sparse infill (always hidden inside) is left out, and very
  large prints are thinned out by layers instead of by points, so walls stay closed. Existing
  thumbnails are redrawn once.

## 0.14.2

- **Printer status text follows the dot:** in the settings, the text under a printer now changes
  together with the live dot (“Reachable again, fetching files …” / “Not reachable …”) instead of
  showing the result of the last fetch until it runs again.

## 0.14.1

- **New prints fetched right away for all printers:** when a Klipper, Prusa or OctoPrint printer
  starts a file PlateShelf doesn’t have yet, it fetches it immediately and links the print to its
  project – like Bambu printers already did – instead of waiting for the next 10-minute round or
  “Fetch from printers”.

## 0.14.0

- **OctoPrint:** printers running OctoPrint (for example OctoPi on an Ender, Anycubic or
  Prusa MK3) can now be added as a fourth printer type. PlateShelf fetches the G-code files
  from OctoPrint’s storage and records prints with progress and remaining time. Sign-in uses
  an application key; set up in the settings or with `PRINTER_<n>_TYPE=octoprint` and
  `PRINTER_<n>_API_KEY`.

## 0.13.4

- **Clearer printer status dots:** reachable printers now show a green dot and unreachable ones a
  red dot, in fixed colours instead of the accent and warning colours – which could look almost
  the same, for example when a browser extension changes the page colours.

## 0.13.3

- **Print progress in the browser tab:** while a print is running, the tab title starts with its
  progress (“45 % · …”), so you can follow it without switching tabs. The progress in the title
  bar now updates every 15 seconds instead of every 30.

## 0.13.2

- **Live progress on the project page and in the history:** the percentage of a running print
  now follows the printer every 15 seconds there too, like on the overview tiles, instead of
  staying at the value from when the page was loaded. `/api/printing` also returns the id of
  each print (`job`).

## 0.13.1

- **Klipper: running print shown even without Moonraker history:** if Moonraker’s job history
  doesn’t list the current print (for example after a Klipper restart, or with the history
  component switched off), PlateShelf now takes it from Klipper’s print status – with project,
  progress and remaining time – and closes it when the printer finishes.

## 0.13.0

- **Printer status live in the settings:** the dot in front of each printer now shows within a
  few seconds whether it is reachable – switch a printer on and it turns green without reloading.
  When a printer comes back, PlateShelf fetches its files right away instead of waiting for the
  next interval. New endpoint `/api/printers/status`.
- **Shorter thumbnail options:** “With print plate”, “Without print plate” and “From the slicer”.

## 0.12.0

- **3D preview on the phone:** the page no longer scrolls (swiping on the model turned it
  instead of scrolling, so the layer controls were hard to reach). The preview fills the top,
  the layer slider and playback stay visible underneath, and everything else – colouring,
  filaments, extras, view buttons – opens with “Options” as a sheet from the bottom.

## 0.11.4

- **Printer addresses shown alike:** the settings list now shows just the address for every
  printer type – without `http://` and without the default port – and for Prusa and Klipper
  it links to the printer’s web interface. The field is now called “IP address or hostname”:
  names such as `mini.fritz.box` work for all printer types.

## 0.11.3

- **Prusa: missed print ends are really closed:** 0.11.2 only looked at the newest running print
  of a printer, which is usually the current one, so an older print whose end was missed stayed
  “running”. Now every older running print of that printer is closed.

## 0.11.2

- **Live progress on the project tiles:** the “Printing … %” badge in the overview now follows
  the running print every 15 seconds instead of staying at the value from when the page was
  loaded. `/api/printing` also returns the project id of each print (`project`).
- **Prusa: missed print ends are closed:** if a PrusaLink printer was switched off before it
  reported the end of a print, that print no longer stays “running” forever. It is closed when
  the printer reports no print or a new one – as finished if it was almost done, otherwise as
  cancelled.

## 0.11.1

- **No more stray lines during playback:** the path segment being printed right now could be
  drawn as a long line through the nozzle, far beyond the model, whenever the nozzle had just
  started a new segment. It now ends exactly at the nozzle. This was the actual cause of the
  lines, so the “Full quality playback” option from 0.11.0 is gone again.

## 0.11.0

- **Better own thumbnails:** top surfaces are solid again (lower layers no longer show through
  as a grid pattern, e.g. on PrusaSlicer files), and the image is framed on the model itself, so
  flat or star-shaped models fill the picture. Existing thumbnails are redrawn once.
- **“Full quality playback”:** a new option in the 3D preview always shows tubes during playback,
  even on devices where PlateShelf would otherwise switch to thin lines to keep it smooth.

## 0.10.4

- **No more switching between tubes and lines during playback:** the 3D preview picks one display
  for the whole playback (based on the largest range that will be visible) instead of
  re-deciding on every layer change, so the model no longer flips to thin lines and back.

## 0.10.3

- **Own thumbnails without wipe tower and supports:** like the slicer images, PlateShelf’s own
  thumbnails now leave out the prime tower, skirt and supports, and centre the model itself.
  Existing thumbnails are redrawn once in the background.
- **Smoother nozzle in the 3D preview:** during playback the nozzle no longer jumps from the end
  of one path to the start of the next; it travels there like a printer, then printing continues.

## 0.10.2

- **Overview also updates for prints and thumbnails:** it now reloads by itself when a print
  starts, finishes or gets assigned to a project, and when PlateShelf has drawn new thumbnails –
  not just when files change. The progress of a running print still doesn’t cause reloads.
- **Direct import from the Prusa MINI works:** the MINI lists its files without a size, so every
  download was rejected as “incomplete”. PlateShelf now checks the length of the download itself.
  A printer in API-key mode that rejects the credentials now shows “check password” instead of a
  technical error.

## 0.10.1

- **Dashboard: placeholder when idle:** `/api/printing?idle_placeholder=1` returns an entry
  “No print running” instead of an empty list (Homepage otherwise shows “No items found”).
  Each running print also has `remaining` as ready-made text (“1 h 35 min”).

## 0.10.0

- **Own thumbnails:** PlateShelf now draws its own thumbnails from the toolpaths, all in the
  same style (at an angle and from above, in the filament colours, on a plate the size of the
  printer), instead of mixing the very different images from PrusaSlicer, Bambu Studio and
  Cura. They are created in the background with the 3D preview; until then the slicer image
  stays. Files without a thumbnail (e.g. from Cura) now get one too. Under “Settings” →
  “Thumbnails” you can choose images without a plate or go back to the slicer images.

## 0.9.2

- **No more dark stripes on the plate:** in the 3D preview, the dark edge of the plate no longer
  shows through the plate as stripes when looking at it from further away.

## 0.9.1

- **3D preview with the right plate size:** the plate now has the size of the printer the file
  was sliced for (taken from the slicer settings, e.g. 180 × 180 mm for the Prusa MINI) instead
  of always 256 mm, so the model sits where it will be printed.
- **Printer status right after saving:** after saving the printers in the settings, the page
  reloads as soon as they have been checked, so the dot turns green or red straight away instead
  of staying grey.
- **“Fetch from printers” finishes visibly:** the overview now also reloads when the fetch is done
  but brought nothing new, so “Asking the printers …” no longer stays on screen.

## 0.9.0

- **Shorter entries for dashboards:** `/api/printing` now also returns `short` (“P2S: Benchy ·
  32 min”, without the percentage and with long names shortened), plus `printer`, `name` and
  `remaining_min` as separate fields. `text` stays as before. Useful for Homepage’s
  `dynamic-list`, where the progress already shows as its own value.

## 0.8.18

- **Overview updates by itself:** new prints fetched from the printers (or other changes to the
  library) now appear without reloading the page by hand – quickly after “Fetch from printers”,
  otherwise within about 20 seconds. If projects are selected or a field is being edited, a short
  notice with “Refresh” appears instead, so nothing gets lost.

## 0.8.17

- **Documentation in the wiki:** the docs from `docs/` are mirrored into the GitHub wiki on
  every release (with a sidebar for English and German); `docs/` stays the source.
- **Passkeys:** sign in without a password using a fingerprint, face recognition, device PIN or
  security key. Passkeys are added and removed under “My account”; signing in works without a
  user name. Needs HTTPS (or localhost). New dependency `webauthn`.
- **Only passkeys or SSO:** `PASSWORD_LOGIN=off` switches off password sign-in (form and Basic
  Auth); it only takes effect once OIDC is set up or a passkey exists, so nobody gets locked out.
- **Log:** “Settings” → “Log” lists sign-ins, failed attempts, lock-outs and changes to accounts,
  roles, passwords, API tokens and passkeys for 180 days. Database schema 7.
- **Security headers:** strict Content Security Policy, no embedding in other sites,
  `X-Content-Type-Options`, `Referrer-Policy` and `Permissions-Policy` on every response.

## 0.8.16

- **Fix: running print showed the previous project.** When a single-plate print starts, the
  printer only reports the process profile and plate 1, and its file is fetched seconds later.
  Since 0.8.13, PlateShelf assigned such prints at start to the previously fetched file, i.e.
  the previous print, and kept it. Now a file without plate number only counts if it was
  fetched for this print; until then the print stays open and is assigned after the import.
  Prints that were assigned wrongly this way are corrected automatically.

## 0.8.15

- **Settings explained consistently:** explanations now always sit under the heading of a
  section, never under single fields (before, some fields had a note below and others not).

## 0.8.14

- **Faster playback in the 3D preview:** besides 1×, 10× and 50× there are now 200× and
  1000×, so large prints play through in reasonable time. At high speed the preview updates
  once per frame even when several layers pass.

## 0.8.13

- **Fix: prints of single-plate projects get their project again.** For projects with only
  one plate, the file from the printer has no “_plate_1” in its name, and Bambu printers may
  report just the process profile (“0.2mm layer, 2 walls, 15% infill”) as the name. Such
  prints stayed without a file, project, filament and costs in the history and statistics.
  PlateShelf now also looks at the plates inside the file; affected prints from the last
  30 days are linked automatically after the update.

## 0.8.12

- **Price rules instead of a filament list:** under “Settings” → “Costs” you now add free
  rules “keyword → € per kg” (e.g. `PLA` 20, `Silk` 28, `Panchroma` 18). A rule applies to
  every filament whose profile name or material contains the keyword; the longest keyword
  wins. Each rule shows how many plates it matches. Prices entered in 0.8.11 keep working as
  rules. Explained with examples in the usage docs.
- **Fix:** saving “Settings” → “Costs” now returns to “Costs” instead of “Printers”.

## 0.8.11

- **Choose the version of a plate:** if a project holds plates sliced with different layer
  heights (re-sliced and printed again), buttons above the plate strip (e.g. “0.16 mm”,
  “0.20 mm”, “All versions”) show only the plates and files of one version; the newest
  is selected first.
- **Fix:** when all files of a project were deleted outside PlateShelf, reading the library
  stopped with a database error. The empty project is now removed; if the files come back,
  they are assigned again.
- **Filament prices are added, not listed:** “Settings” → “Costs” no longer lists every
  filament profile of the library. You add prices yourself (“Add filament price”, with the
  profiles from your files as suggestions while typing) and only see the ones you added.

## 0.8.10

- **Filament prices in PlateShelf:** under “Settings” → “Costs” (formerly “Electricity
  costs”) you can set a price per kg for every filament in your library; it applies to all
  prints, also retroactively, and takes precedence over the price in the file. A default
  price per kg covers files without any price (Cura, profiles with €0); with “Ignore prices
  from slicer files” it applies to every filament without its own price, for slicer
  profiles whose prices were never maintained. Project costs, statistics and the API use
  the prices right away.
- **Plate sliced twice:** when a project holds the same plate in several files (re-sliced and
  printed again), the plate strip shows each version with its date and layer height, newest
  first, instead of two identical entries.
- **`OIDC_HOSTS`:** shows “Sign in with …” only on the listed host names. Useful when
  PlateShelf is also reachable from outside (e.g. through a tunnel) but the provider only at
  home; elsewhere the page shows just the password sign-in.

## 0.8.9

- **Prints in the old “Vom Drucker” folder are separate projects again:** installations from
  the Druckarchiv days still keep many prints in the former import folder “Vom Drucker”.
  Since 0.7.8 it was no longer treated as a printer folder, so as soon as its files were read
  again (in 0.8.8 by the date fix), all of them collapsed into a single project. PlateShelf
  now groups that folder by file name again and reads the library once more on start
  (database schema 6), which brings the projects back with their notes, tags and photos.
- Projects that have their own cover photo are no longer removed when their files are gone.

## 0.8.8

- **Single sign-on (OIDC):** sign in with Authelia, Authentik, Keycloak, Pocket ID and other
  OpenID Connect providers, alongside the password. Accounts are linked by user name on the
  first sign-in or created; roles can follow groups (`OIDC_ADMIN_GROUP`, `OIDC_USER_GROUP`).
  Authorization code flow with PKCE and signature check of the ID token. Database schema 5.
- **Old prints no longer show up as new:** Bambu printers list files older than about six
  months with the year instead of the time (“Apr 03 18:18” becomes “Apr 03 2026”).
  PlateShelf took that as a changed file, fetched it again and put the project at the top of
  “Newest first”. Files from the printer now keep the date the printer reports; files that
  were fetched again by mistake get their old date back on the next sync.
- **iPhone:** the top bar now reaches up under the status bar, so Safari no longer shows a
  blurred strip of the page above it; in landscape nothing hides behind the camera cutout.
  The browser bar takes the colour of the chosen theme even when it differs from the system.

## 0.8.7

- **3D preview on phones:** the loading bar is now exactly centered, and the 3D view keeps
  some space below the top bar with rounded corners instead of looking cut off.

## 0.8.6

- **Plates printed:** projects with several plates show how many are printed (“3/4 plates
  printed”) in the overview; the project page marks each printed plate with a check mark and
  “Printed (2×)”. New overview filter “Partly printed” for projects with plates still to print.

## 0.8.5

- **One look across all pages:** the same corner radius, page widths and heading sizes
  everywhere; the sidebar of the project page now shows photos, print history and filament
  in the same boxes as the project details; file pickers match the other buttons.
- Fixed: on the project page, a print with a note squeezed the date of the print into a
  narrow column (one character per line).
- Fixed: “Note” in the print history was indented and had a stray line.
- The notice to set up a printer no longer appears in the history when prints are listed.
- The About page lists the current features (accounts, backups, photos, notes per print).
- Screenshots updated.

## 0.8.4

- **Photos of the print:** add your own photos on the project page, for example straight from
  the phone. The first one becomes the cover in the overview. Photos are rotated as taken,
  shrunk to 2400 pixels and stripped of all metadata including GPS. They lie in the library
  under `.photos/` and follow the project into the trash and back.
- **Note and rating per print:** in the history and on the project page, mark a print as
  turned out well or failed and note what happened.
- **New files show up right away** instead of after up to two minutes. The regular scan
  stays as a fallback, e.g. for network shares (`WATCH_LIBRARY`, `SCAN_INTERVAL`).
- **Duplicate files:** the project page points out identical copies of a file elsewhere in
  the library.

## 0.8.3

- **Automatic backups:** once a day PlateShelf saves the database to `backups/` in the data
  folder and keeps the last 7 (`BACKUP_KEEP`, `0` = off). The settings list them for
  download.
- **Restore in the interface:** upload a backup or pick an automatic one under “Settings” →
  “Backup”. The current state is saved first; preview images are rebuilt afterwards.
- **Edit several projects at once:** tick projects in the overview, then set the status, add
  a tag or move them all to the trash.
- **Project as ZIP:** “All files as ZIP” on the project page, with a text file holding
  status, tags, source and notes.
- **Home screen:** PlateShelf can be installed like an app on phones, tablets and in
  Chrome/Edge (web app manifest and icons).
- Quality: CI now runs the Ruff linter and scans the image with Trivy; the browser test
  checks every page for accessibility problems (axe-core, WCAG 2 A/AA) in both themes.

## 0.8.2

- The Barlow fonts are now served by PlateShelf itself instead of Google Fonts. No page
  loads anything from other servers any more, so no IP addresses go to Google and the
  interface looks the same without internet access. The fonts are under the SIL Open Font
  License (`app/static/fonts/OFL.txt`).
- Screenshots updated (settings with users and API tokens).

## 0.8.1

- Reloading the page after creating an API token (or after an error in a settings or
  password form) no longer shows “Method Not Allowed”; it goes back to that section.

## 0.8.0

- **User accounts replace the single sign-in from the `.env`.** Sign-in is now on by
  default. On the first start, PlateShelf asks you to create the admin account; the page
  needs a setup code from the container log, so nobody else on the network can take over.
  Existing `AUTH_USER` and `AUTH_PASSWORD_HASH` become the first admin account
  automatically. `AUTH=off` switches sign-in off, e.g. behind a proxy that signs users in.
- **Roles:** admin (everything), user (everything except settings) and read only (view and
  download). Admins manage accounts under “Settings” → “Users”.
- **Real sessions:** signing out ends the session on the server. “My account” shows the
  signed-in devices, signs out single devices or all others and changes the password.
  Sessions survive restarts and no longer depend on `SECRET_KEY`.
- **API tokens** for dashboards such as Homepage: read only, limited to `/api/`, revocable,
  stored as a hash. Basic Auth with user name and password still works.
- Console: `python -m app.auth reset <name>` sets a new password or creates an admin account;
  `python -m app.auth setup-code` shows the setup code.

## 0.7.13

- Print history on Bambu printers: older prints that only know the plate are now matched
  to the plate file uploaded last before the print started. This also covers plates
  uploaded well before printing and plates uploaded again later.

## 0.7.12

- Print history on Bambu printers such as the P2S: the printer reports only the plate
  (“plate_2.gcode”) instead of the file name. Prints are now matched to the imported
  plate file by plate number and project name, so statistics show filament and costs.
  Prints that are already in the history are linked automatically via the import time.

## 0.7.11

- Phones: long names without spaces (print profiles, objects, plates, links in the
  designer's description) no longer make the project page wider than the screen.
- Safari on iPhone no longer turns version numbers such as “02.08.02.61” into phone links.

## 0.7.10

- **Phones:** the top bar stays compact, with the menu behind a button and the overview's
  filters behind “Filters”. The 3D preview fills the screen below the top bar and leaves
  the controls peeking out underneath, instead of being taller than the screen. Tables in
  the print history and statistics no longer stick out past the edge.
- History, trash, settings and upload are centred on wide screens like the about page,
  instead of sticking to the left.

## 0.7.9

- **Faster with large libraries:** the prepared project data is kept until the database
  changes, and the overview shows 60 projects at a time with a “Show more” button. With
  2000 files the overview went from 1.2 s to 0.05 s, the project page from 0.8 s to
  0.06 s and `/api/stats` from 0.8 s to 0.02 s. `tools/load_test.py` measures this.
- **Statistics:** new tab on the history page with the last twelve months: finished
  prints, print time, filament and costs, prints and costs per month, filament by
  material and the success rate per printer.
- **Backup:** “Settings” → “Backup” downloads the database as a single file, with
  instructions for restoring. After a restore, preview images are rebuilt automatically.
- **Database schema version:** the database now carries a version number; all tables are
  defined in one place, and older databases are brought up to date once on start.

## 0.7.8

- **Printer credentials are stored encrypted** (AES-256-GCM) in the database, with a key
  derived from `SECRET_KEY`. Existing credentials are encrypted automatically on the first
  start with a `SECRET_KEY`. If the key no longer matches, the printer shows a clear
  message and the credentials have to be entered again.
- **`SECRET_KEY` only comes from the environment** (`SECRET_KEY` or `SECRET_KEY_FILE`). The
  key file PlateShelf used to create in the data folder (`secret.key`) is no longer used
  and is removed on start. Without `SECRET_KEY`, credentials stay unencrypted and sign-ins
  last until the next restart; the settings page points this out. Create a key with
  `docker compose exec plateshelf python -m app.auth secret`.
- **Sign-in password as a hash:** `AUTH_PASSWORD_HASH` (scrypt), created with
  `docker compose exec plateshelf python -m app.auth hash`. `AUTH_PASSWORD` in plain text
  still works.
- **Secrets from files:** every sensitive variable also accepts `_FILE` (Docker secrets):
  `SECRET_KEY_FILE`, `AUTH_PASSWORD_HASH_FILE`, `AUTH_PASSWORD_FILE`, `AUTH_USER_FILE`,
  `PRINTER_<n>_CODE_FILE`, `PRINTER_<n>_PASSWORD_FILE`, `PRINTER_<n>_API_KEY_FILE`.
- Upgrading: after the update you have to sign in again once, because sessions are now
  signed with a key derived from `SECRET_KEY`.
- New dependency `cryptography` (Apache-2.0 or BSD).
- **No more image for 32-bit ARM** (arm/v7). Images are built for amd64 and arm64; a
  Raspberry Pi 4/5 needs a 64-bit OS.
- **Upgrade support for versions before 0.7 removed:** renaming `druckarchiv.sqlite`, the
  old trash folder `.papierkorb`, the import folder `Vom Drucker` and the redirects from the
  old German addresses (`/einstellungen`, `/papierkorb`, …). Coming from an older version,
  update to 0.7.7 first and start it once.

## 0.7.7

- **Running prints live:** the top bar shows printer, project, progress and remaining time
  of a running print (refreshed every 30 seconds), the project card shows “Printing 45 %”.
  Remaining time comes from Bambu and PrusaLink; for Klipper it is estimated from the
  progress. `/api/stats` has `printing_name`, `printing_printer`, `printing_progress` and
  `printing_remaining_min` for dashboards.
- **Overview by print history:** sort by “Recently printed” or “Most printed”, filter by
  “Printed before” or “Never printed”.
- **Diagnostics** under “Settings”: a text to copy into a GitHub issue with version,
  platform, library figures, printer types and their last result, which settings are set
  and the latest warnings, without credentials, IP addresses or printer names.
- **Issue forms on GitHub** for bug reports and feature requests.
- README screenshots now include the print history and the settings page.
- Internal: `app/main.py` is split into `web.py`, `projects.py` and `routes_*.py`. The tests
  no longer start background threads; lingering threads from earlier tests occasionally
  accessed the database of a later test and made the suite fail at random.

## 0.7.6

- **New settings page:** the sections (printers, electricity costs, print history) are
  listed on the left and shown one at a time; on phones they become a row of buttons at
  the top. Printers appear as a compact list with status, type and address; the fields
  only open with “Edit” or “Add printer”, together with the hints for the chosen type.
  The `.env` examples moved into a collapsible note.
- The print history is a switch that saves right away.

## 0.7.5

- Prints can be removed from the print history (“×” on the history and project page),
  e.g. test prints. They no longer count and are not re-added by Moonraker or PrusaLink.
  If the print had set the project to “Printed” and no other finished print remains, the
  status is reset; a status you changed yourself stays.
- The print history can be switched on and off under “Settings” → “Print history”.
  `PRINT_HISTORY` in the `.env` is now optional: set, it fixes the setting and hides the
  switch. When switched off, the MQTT connections to Bambu printers are closed right away
  and “History” disappears from the top bar.

## 0.7.4

- **Print history:** PlateShelf records every print of the printers set up for the direct
  import, with start, end, duration and outcome (finished, cancelled, failed). Bambu
  printers report via MQTT, Klipper printers via Moonraker’s history, Prusa printers are
  queried via PrusaLink every minute. Each print is matched to the imported file and its
  project.
- New page “History” in the top bar; the project page lists the prints of its files and
  the overview shows how often a project was printed.
- A finished print sets a project without status to “Printed”.
- `/api/stats` has new fields: `prints_30d`, `prints_failed_30d`, `printing_now`,
  `last_print_end`.
- New settings `PRINT_HISTORY` (default `true`) and `PRINT_HISTORY_INTERVAL` (60 s).
- New dependency `paho-mqtt` (EPL-2.0 or BSD-3-Clause) for the connection to Bambu
  printers.

## 0.7.3

- **Binary G-code** (`.bgcode`) from PrusaSlicer, the default for Prusa MK4, XL, Mini and
  Core One: print time, filament, colours, costs, printer, settings, object names and the
  preview image are read from the metadata blocks; the 3D preview unpacks the G-code
  (Heatshrink and MeatPack). Uploading accepts `.bgcode`.
- **Direct import from Prusa printers via PrusaLink** (MK4, MK3.9, XL, Mini, Core One):
  choose “Prusa (PrusaLink)” in the settings and enter the PrusaLink password, or use
  `PRINTER_<n>_TYPE=prusalink` with `PRINTER_<n>_IP` and `PRINTER_<n>_PASSWORD`. Files are
  fetched from the USB drive and internal storage, including subfolders.
- 3D preview: G-code without layer comments now gets its layers from the print height,
  and progress while preparing a preview is reported more evenly.
- New dependency `heatshrink2` (ISC license) for unpacking `.bgcode`.

## 0.7.2

- **Plain G-code files** (`.gcode`) from PrusaSlicer, SuperSlicer, OrcaSlicer, Cura,
  Creality Print and similar slicers. PlateShelf reads print time, layers, filament usage,
  colours, costs, printer and key settings from the slicer comments, shows the embedded
  preview image (including Prusa’s QOI thumbnails) and builds the 3D preview with line
  types for PrusaSlicer, OrcaSlicer and Cura. They can be uploaded like `.gcode.3mf`
  files.
- **Direct import from Klipper printers via Moonraker**, e.g. Creality K1/K2, Sovol SV08,
  Qidi, Elegoo Neptune 4 or Voron. Choose the printer type in the settings, or use
  `PRINTER_<n>_TYPE=moonraker` with `PRINTER_<n>_IP` and an optional
  `PRINTER_<n>_API_KEY` in the `.env`. Existing Bambu printers keep working unchanged.

## 0.7.1

- The trash folder inside the library is now called `.trash` (previously `.papierkorb`),
  independent of the interface language. Existing `.papierkorb` folders are renamed
  automatically on first access; deleted items can still be restored. If the library is
  mounted read-only, the old folder keeps being used.
- English URLs: `/settings`, `/trash` (with `/trash/<id>/restore`, `/trash/<id>/delete`,
  `/trash/empty`) and `/appearance`, plus English query parameters and anchors
  (e.g. `/settings#power`). The old German addresses redirect permanently, including
  submitted forms, so bookmarks and open tabs keep working.

## 0.7.0

- **Druckarchiv is now PlateShelf.** New name in the interface, the repository
  (`github.com/schnuecks/plateshelf`) and the image (`ghcr.io/schnuecks/plateshelf`).
  Existing installations keep their data: `druckarchiv.sqlite` is renamed to
  `plateshelf.sqlite` automatically, an existing `Vom Drucker` import folder keeps being
  used, and browser settings are kept. In your `.env`, replace `DRUCKARCHIV_IMAGE` with
  `PLATESHELF_IMAGE` (see “Upgrading from Druckarchiv” in the README).
- New logo, also used as the browser icon.
- New “About PlateShelf” page (link in the footer) with what PlateShelf does, version,
  links and license.
- English is now the default language when the browser language isn’t one of the six
  supported ones (previously German).
- The default folder for the direct import is now `From printer` (new installations).
- README in English with screenshots; the German README is kept as `README.de.md`.
  `.env.example` and `compose.yaml` are commented in English.
- `tools/make_screenshots.py` recreates the README screenshots.

## 0.6.0 and older

The first versions (2026, still called *Druckarchiv*) are described in German in the git
history of this file.
