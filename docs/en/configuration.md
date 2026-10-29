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
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` (`compose.yaml`; image: local and private networks) | Reverse proxies whose `X-Forwarded-For` is trusted, see [Installation](installation.md#behind-a-reverse-proxy) |

**Library**

| Variable | Default | Meaning |
|---|---|---|
| `SCAN_INTERVAL` | 120 | Seconds between two full scans of the library (fallback, e.g. for network shares) |
| `WATCH_LIBRARY` | true | Detect new and changed files right away; `false` relies on `SCAN_INTERVAL` only |
| `MAX_UPLOAD_MB` | 1024 | Size limit per uploaded file in MB |
| `PREVIEW_PREBUILD` | true | Prepare 3D previews in the background after reading files |
| `TRASH_DAYS` | 30 | Days until items are deleted permanently from the trash (0 = never) |
| `BACKUP_KEEP` | 7 | Automatic daily backups to keep in `backups/` of the data folder (0 = off) |

**Direct import**

| Variable | Default | Meaning |
|---|---|---|
| `PRINTER_<n>_NAME`, `_IP`, `_SERIAL`, `_CODE` | – | Bambu printers for the direct import (optional) |
| `PRINTER_<n>_TYPE` | `bambu` | `prusalink` for Prusa, `moonraker` for Klipper printers, `octoprint` for OctoPrint |
| `PRINTER_<n>_PASSWORD` | – | PrusaLink password (Prusa) |
| `PRINTER_<n>_API_KEY` | – | Moonraker API key, only if required (Klipper); application key (OctoPrint) |
| `PRINTER_SYNC_INTERVAL` | 600 | Seconds between two printer queries |
| `PRINTER_IMPORT_DIR` | From printer | Target folder for imported prints in the library |
| `PRINT_HISTORY` | – | `true` or `false` fixes the print history; unset, the switch in the settings applies (default on) |
| `PRINT_HISTORY_INTERVAL` | 60 | Seconds between two queries of Klipper, Prusa and OctoPrint printers |
| `PRINT_SEND` | – | `true` or `false` fixes “Print” for Bambu Lab printers; unset, the switch in the settings applies (default off) |

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
| `PASSWORD_LOGIN` | on | `off` allows signing in only with passkeys or OIDC, see [Passkeys](#passkeys) |
| `AUTH_USER`, `AUTH_PASSWORD_HASH` | – | Only for updates from before user accounts: become the first admin account once |
| `SECRET_KEY` | – | Key for encrypting printer credentials; strongly recommended, see [Secrets](#secrets) |
| `TRUSTED_ORIGINS` | – | Additional addresses allowed to send changes, comma-separated |
| `AUTH_PORTAL_URL` | – | Sign-in page of a reverse proxy with login (e.g. `https://auth.example.com`), comma-separated; see below |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | – | Sign in with Authelia, Authentik, Keycloak & co., see [Single sign-on](#single-sign-on-oidc) |

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
address is blocked for five minutes. In addition, a user name is blocked after 20 failed
attempts within 15 minutes from any number of addresses; only addresses the account has
already signed in from are exempt, so you are not locked out yourself. This also applies to
HTTP Basic Auth. Whether a user name exists cannot be told from the response or its timing.

**Scripts** sign in with user name and password via HTTP Basic Auth; the role applies here
too. For dashboards, [API tokens](dashboard.md#api-tokens) are the better choice.

**Locked out?** Set a new password on the console; if the account does not exist, an admin
account with that name is created:

```bash
docker compose exec plateshelf python -m app.auth reset <name>
```

**Passkeys:** under “My account” → “Passkeys”, everyone can add passkeys (fingerprint, face
recognition, device PIN or a security key) and then sign in with “Sign in with a passkey”,
without user name and password. A passkey belongs to the address where it was created, e.g.
`plateshelf.example.com`, and needs HTTPS (or `localhost`); on a plain `http://` address the
page says so. Removing a passkey in “My account” takes effect immediately.

**Only passkeys or SSO:** `PASSWORD_LOGIN=off` switches off signing in with a password, both
on the sign-in page and via Basic Auth for scripts (API tokens keep working). To prevent a
lock-out it only takes effect once there is another way in: OIDC is set up or at least one
passkey exists. To undo it, remove the line from the `.env`.

**Log:** “Settings” → “Log” lists sign-ins (with method: password, passkey or OIDC), failed
attempts, lock-outs, sign-outs and changes to accounts, roles, passwords, API tokens and
passkeys, with device and address, for the last 180 days. Failures are highlighted.

**Security headers:** every response carries a strict Content Security Policy (only
PlateShelf's own scripts and files, no embedding in other sites), plus `X-Frame-Options`,
`X-Content-Type-Options`, `Referrer-Policy` and `Permissions-Policy`. HSTS is left to the
reverse proxy that terminates HTTPS.

**Without sign-in:** `AUTH=off` in the `.env` switches all of this off; anyone who can reach
PlateShelf may then do everything. This only makes sense if the reverse proxy signs users
in (e.g. Authelia or Authentik) or PlateShelf is not reachable by anyone else.

**Reverse proxy with a login page:** the Content Security Policy only lets forms go to
PlateShelf itself and to the OIDC provider. If the proxy's session runs out and it redirects a
form (e.g. saving the settings) to its login page, the browser blocks that and nothing seems to
happen. Set `AUTH_PORTAL_URL` to the address of that login page, e.g.
`AUTH_PORTAL_URL=https://auth.example.com`; only HTTPS addresses (or `localhost`) count.

`/healthz` stays reachable for the Docker health check, but without sign-in it only reports
that PlateShelf is running.

**Protection against other websites:** Regardless of the above, PlateShelf only accepts
changes (upload, delete, settings …) from its own pages. Forms that another website
sends to PlateShelf in the background are rejected. What counts is the address the
browser sends along; behind a proxy, the forwarded host (`X-Forwarded-Host`) counts.
Scripts without a browser (e.g. `curl`) are not affected. To allow additional sites to
send changes, add them to `TRUSTED_ORIGINS`, for example
`TRUSTED_ORIGINS=https://dashboard.example.com`.

### Single sign-on (OIDC)

Besides the password, PlateShelf can let people sign in with an OpenID Connect provider such
as Authelia, Authentik, Keycloak, Pocket ID or Kanidm. The sign-in page then shows an extra
button “Sign in with …”; signing in with a password keeps working.

| Variable | Default | Meaning |
|---|---|---|
| `OIDC_ISSUER` | – | Address of the provider (issuer), e.g. `https://auth.example.com`; switches OIDC on |
| `OIDC_CLIENT_ID` | – | Client ID registered at the provider |
| `OIDC_CLIENT_SECRET` | – | Client secret (also as `OIDC_CLIENT_SECRET_FILE`); empty for a public client with PKCE only |
| `OIDC_NAME` | SSO | Name on the button, e.g. `Authelia` |
| `OIDC_ADMIN_GROUP` | – | Members of this group become admins |
| `OIDC_USER_GROUP` | – | Members of this group become users |
| `OIDC_GROUPS_CLAIM` | groups | Claim that holds the groups |
| `OIDC_SCOPES` | openid profile email groups | Requested scopes |
| `OIDC_HOSTS` | – | Only offer the button on these host names (comma-separated), e.g. `plateshelf.home.lan` when the provider is only reachable at home |

At the provider, register PlateShelf as a client with the redirect URI
`https://<your PlateShelf address>/login/oidc/callback`. Example for Authelia:

```yaml
identity_providers:
  oidc:
    clients:
      - client_id: 'plateshelf'
        client_name: 'PlateShelf'
        client_secret: '$pbkdf2-sha512$...'   # hash of the secret, see the Authelia docs
        authorization_policy: 'two_factor'
        require_pkce: true
        pkce_challenge_method: 'S256'
        redirect_uris:
          - 'https://plateshelf.example.com/login/oidc/callback'
        scopes: ['openid', 'profile', 'email', 'groups']
        token_endpoint_auth_method: 'client_secret_basic'
```

and in PlateShelf's `.env`:

```env
OIDC_ISSUER=https://auth.example.com
OIDC_CLIENT_ID=plateshelf
OIDC_CLIENT_SECRET=…
OIDC_NAME=Authelia
OIDC_ADMIN_GROUP=admins
OIDC_USER_GROUP=family
```

**Accounts:** on the first sign-in, PlateShelf creates a new account (named after
`preferred_username`). From then on the account stays linked to the person at the provider,
even if the name changes there. Accounts created this way have no password in PlateShelf;
they only sign in with the provider.

An **existing account** is never taken over by its name, because many providers let people
choose their name freely. Its owner signs in as usual and links it under “My account” →
“Link with …”; from then on the provider signs in to this account. If someone signs in with
the provider under a name that already exists, PlateShelf points them there.

**Roles:** with `OIDC_ADMIN_GROUP` and/or `OIDC_USER_GROUP`, the role follows the groups on
every sign-in: admin group → Admin, user group → User, neither → Read only. Without these
variables, new accounts start as Read only and admins change the role in PlateShelf. The
last admin always keeps the role.

**First start:** the first account is created with the setup code as usual, or, if
`OIDC_ADMIN_GROUP` is set, by a member of the admin group signing in with the provider.

PlateShelf uses the authorization code flow with PKCE, checks the ID token's signature,
issuer, audience, expiry and nonce, and binds the sign-in to the browser that started it.
Signing out of PlateShelf does not sign you out of the provider.

If the provider uses a certificate from your own CA, mount the CA certificate into the
container and point `SSL_CERT_FILE` to it.

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
file by adding `_FILE`: `SECRET_KEY_FILE`, `AUTH_PASSWORD_HASH_FILE`, `AUTH_PASSWORD_FILE`,
`OIDC_CLIENT_SECRET_FILE` and `PRINTER_<n>_CODE_FILE`, `…_PASSWORD_FILE`, `…_API_KEY_FILE`. This keeps them out of the
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
