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
| `AUTH` | on | `off` switches sign-in off, e.g. when the reverse proxy already signs users in |
| `SESSION_DAYS` | 30 | How long a sign-in lasts in the browser |
| `AUTH_USER`, `AUTH_PASSWORD_HASH` | – | Only for updates from before user accounts: become the first admin account once |
| `SECRET_KEY` | – | Key for encrypting printer credentials; strongly recommended, see [Secrets](#secrets) |
| `TRUSTED_ORIGINS` | – | Additional addresses allowed to send changes, comma-separated |

## Access control

PlateShelf asks everyone to sign in. Accounts, passwords and roles are managed in
PlateShelf itself; nothing has to go into the `.env`.

**First start:** as long as there is no account, every page leads to “Create admin
account”. The page asks for a setup code that PlateShelf writes to the container log, so
nobody else on your network can create the account before you:

```bash
docker compose logs plateshelf | grep "Setup code"
```

Alternatively, `docker compose exec plateshelf python -m app.auth setup-code` prints it.
Enter the code, a user name and a password (at least 8 characters), and you are signed in
as admin.

**Updating from an older version:** if `AUTH_USER` and `AUTH_PASSWORD_HASH` (or
`AUTH_PASSWORD`) are set, PlateShelf turns them into the first admin account on start. You
sign in as before; afterwards the two lines can be removed from the `.env`, and the password
is changed in PlateShelf. Dashboards that used these credentials via Basic Auth keep working.

**Roles:** under “Settings” → “Users”, admins add accounts and pick a role:

| Role | May |
|---|---|
| Admin | everything, including settings, users and API tokens |
| User | upload, edit, move and delete files and projects, empty the trash; no settings |
| Read only | view and download, switch language and theme, change their own password |

At least one admin always remains. Admins can set a new password for any account; that
signs the account out on all devices.

**Own account:** the person icon in the top right opens “My account”:

- change your password; all your other devices are signed out
- see where you are signed in (browser and system, when, last active) and sign out
  single devices or all others

**Sessions:** a sign-in lasts `SESSION_DAYS` days. The browser only gets a random value;
the database only stores its hash. Signing out ends the session on the server right away,
so a copied cookie is useless afterwards. Sessions survive restarts, with or without
`SECRET_KEY`.

**Protection against guessing:** after five failed attempts (password or setup code), an
address is blocked for five minutes. Whether a user name exists cannot be told from the
response or its timing.

**Scripts** sign in with user name and password via HTTP Basic Auth; the role applies here
too. For dashboards, [API tokens](dashboard.md#api-tokens) are the better choice.

**Locked out?** Set a new password on the console; if the account does not exist, an admin
account with that name is created:

```bash
docker compose exec plateshelf python -m app.auth reset <name>
```

**Without sign-in:** `AUTH=off` in the `.env` switches all of this off; anyone who can reach
PlateShelf may then do everything. This only makes sense if the reverse proxy signs users
in (e.g. Authelia or Authentik) or PlateShelf is not reachable by anyone else.

`/healthz` stays reachable for the Docker health check, but without sign-in it only reports
that PlateShelf is running.

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
`SECRET_KEY`, PlateShelf still works, but stores printer credentials unencrypted; the
settings page points this out. User passwords are always stored as scrypt hashes and
never need the key.

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
