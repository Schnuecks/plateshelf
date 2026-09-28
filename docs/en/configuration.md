# Configuration

**English** · [Deutsch](../de/configuration.md) · [← Documentation](README.md)

## Settings

All settings live in the `.env`. The `compose.yaml` passes them to the container.

**Container and folders**

| Variable | Default | Meaning |
|---|---|---|
| `PLATESHELF_IMAGE` | `ghcr.io/schnuecks/plateshelf:latest` | Image to use |
| `PUID`, `PGID` | 1000 | User and group the container runs as |
| `TZ` | Europe/Berlin | Time zone |
| `PORT` | 8000 | Port on the host |
| `LIBRARY` | `./library` | Folder with the `.gcode.3mf`, `.gcode` and `.bgcode` files |
| `DATA` | `./data` | Folder for database, preview images and 3D previews |
| `LOG_LEVEL` | INFO | Log verbosity (`docker compose logs`): `DEBUG`, `INFO` or `WARNING` |

**Library**

| Variable | Default | Meaning |
|---|---|---|
| `SCAN_INTERVAL` | 120 | Seconds between two scans of the library |
| `MAX_UPLOAD_MB` | 1024 | Size limit per uploaded file in MB |
| `PREVIEW_PREBUILD` | true | Prepare 3D previews in the background after reading files |
| `TRASH_DAYS` | 30 | Days until items are deleted permanently from the trash (0 = never) |

**Direct import**

| Variable | Default | Meaning |
|---|---|---|
| `PRINTER_<n>_NAME`, `_IP`, `_SERIAL`, `_CODE` | – | Bambu printers for the direct import (optional) |
| `PRINTER_<n>_TYPE` | `bambu` | `prusalink` for Prusa, `moonraker` for Klipper printers |
| `PRINTER_<n>_PASSWORD` | – | PrusaLink password (Prusa) |
| `PRINTER_<n>_API_KEY` | – | Moonraker API key, only if required (Klipper) |
| `PRINTER_SYNC_INTERVAL` | 600 | Seconds between two printer queries |
| `PRINTER_IMPORT_DIR` | From printer | Target folder for imported prints in the library |
| `PRINT_HISTORY` | – | `true` or `false` fixes the print history; unset, the switch in the settings applies (default on) |
| `PRINT_HISTORY_INTERVAL` | 60 | Seconds between two queries of Klipper and Prusa printers |

**Interface**

| Variable | Default | Meaning |
|---|---|---|
| `DEFAULT_LANGUAGE` | – | Default language: `en`, `de`, `fr`, `es`, `it` or `nl`; empty: browser language |
| `DEFAULT_THEME` | system | Default theme: `system`, `light` or `dark` |

**Access control**

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | – | Key for encrypting printer credentials and signing sessions; strongly recommended, see [Secrets](#secrets) |
| `AUTH_USER`, `AUTH_PASSWORD_HASH` | – | Turn on sign-in with a password hash |
| `AUTH_PASSWORD` | – | Alternative to the hash: password in plain text |
| `SESSION_DAYS` | 30 | How long a sign-in lasts in the browser |
| `TRUSTED_ORIGINS` | – | Additional addresses allowed to send changes, comma-separated |

## Access control

Out of the box, PlateShelf has no sign-in: anyone who can reach it can upload and
delete files. On a home network that is usually fine. For more protection there are two
options, which can also be combined.

**Built-in sign-in:** set `AUTH_USER` and a password in the `.env`. Store the password
as a hash rather than in plain text:

```bash
docker compose exec plateshelf python -m app.auth hash
```

The command asks for the password twice and prints a line such as
`AUTH_PASSWORD_HASH=scrypt:15:8:1:…`, which goes into the `.env` next to `AUTH_USER`.
`AUTH_PASSWORD=…` in plain text still works.

- The browser then shows a sign-in page. A sign-in lasts `SESSION_DAYS` days, and “Sign
  out” appears in the top right.
- After five failed attempts, an address is blocked for five minutes.
- Changing the password ends all existing sessions.
- Scripts and dashboards sign in with the same credentials via HTTP Basic Auth.
- `/healthz` stays reachable for the Docker health check, but without sign-in it only
  reports that PlateShelf is running.

**Sign-in at the reverse proxy**, e.g. with Authelia or Basic Auth. This is recommended
especially if PlateShelf is reachable from the internet.

**Protection against other websites:** Regardless of the above, PlateShelf only accepts
changes (upload, delete, settings …) from its own pages. Forms that another website
sends to PlateShelf in the background are rejected. What counts is the address the
browser sends along; behind a proxy, the forwarded host (`X-Forwarded-Host`) counts.
Scripts without a browser (e.g. `curl`) are not affected. To allow additional sites to
send changes, add them to `TRUSTED_ORIGINS`, for example
`TRUSTED_ORIGINS=https://dashboard.example.com`.

## Secrets

PlateShelf needs the access codes, passwords and API keys of your printers in readable
form to connect to them. They are therefore not hashed but encrypted (AES-256-GCM) in
the database. The key is derived from `SECRET_KEY`, which PlateShelf only takes from the
environment, never from the data folder. A copy of the database alone, for example from
a backup, is therefore useless.

```bash
docker compose exec plateshelf python -m app.auth secret
```

prints a random `SECRET_KEY=…`; put it into the `.env` and restart PlateShelf. Existing
credentials are then encrypted automatically. Keep the key safe: without it the stored
credentials cannot be decrypted, and you would have to enter them again. Without
`SECRET_KEY`, PlateShelf still works, but stores credentials unencrypted and sign-ins only
last until the next restart; the settings page points this out.

**Secrets as files (Docker secrets):** every sensitive variable can also be read from a
file by adding `_FILE`: `SECRET_KEY_FILE`, `AUTH_PASSWORD_HASH_FILE`, `AUTH_PASSWORD_FILE`
and `PRINTER_<n>_CODE_FILE`, `…_PASSWORD_FILE`, `…_API_KEY_FILE`. This keeps them out of the
`.env` and out of `docker inspect`:

```yaml
services:
  plateshelf:
    environment:
      SECRET_KEY_FILE: /run/secrets/plateshelf_key
    secrets: [plateshelf_key]
secrets:
  plateshelf_key:
    file: ./secrets/plateshelf_key.txt
```
