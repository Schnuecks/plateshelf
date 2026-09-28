# Installation

**English** · [Deutsch](../de/installation.md) · [← Documentation](README.md)

## Requirements

You need Docker with Docker Compose 2.24 or newer. Images are available for PCs and
servers (amd64) and 64-bit ARM (arm64, e.g. Raspberry Pi 4/5 with a 64-bit OS and many NAS
devices). Docker picks the right one automatically.

## Quick start

```bash
mkdir plateshelf && cd plateshelf
curl -O https://raw.githubusercontent.com/schnuecks/plateshelf/main/compose.yaml
curl -o .env https://raw.githubusercontent.com/schnuecks/plateshelf/main/.env.example
mkdir -p library data
docker compose up -d
```

PlateShelf is now available at `http://<server>:8000`. On the first visit you create
the admin account; the setup code it asks for is in the log (`docker compose logs
plateshelf`), see [Access control](configuration.md#access-control). Put your `.gcode.3mf`, `.gcode` or `.bgcode` files into
the `library` folder. They show up within two minutes, or immediately via “Rescan
library” in the overview.

The `library` and `data` folders must be writable for the user the container runs as
(`PUID` and `PGID` in the `.env`, default 1000). Otherwise startup fails with “unable
to open database file”.

## Updating

```bash
docker compose pull && docker compose up -d
```

The database is upgraded automatically on startup if needed. If the format of the 3D
preview changes, the previews are rebuilt once in the background. What changed in each
version is listed in the [CHANGELOG](../../CHANGELOG.md). Coming from a version before 0.7
(including the old name *Druckarchiv*), update to 0.7.7 first and start it once.

## Behind a reverse proxy

PlateShelf listens on port 8000 inside the container and needs no special proxy
settings. An example for Traefik (instead of `ports:` in the `compose.yaml`):

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.plateshelf.rule=Host(`plateshelf.example.com`)
      - traefik.http.services.plateshelf.loadbalancer.server.port=8000
```

## Data and backups

- Files in the library are never modified, only moved to the trash when you delete them.
- The database (SQLite), preview images, 3D previews and your notes are stored in the
  data folder.
- **Backup:** “Settings” → “Backup” downloads the database as a single file, consistent
  even while PlateShelf is running. It contains projects with notes, tags and status,
  assignments, settings, printers and the print history. Back up the library folder with
  the print files as usual. Keep `SECRET_KEY` somewhere safe as well, because the printer
  credentials in the backup can only be read with it.
- **Restore or move:** stop PlateShelf, put the backup into the data folder as
  `plateshelf.sqlite` (delete `plateshelf.sqlite-wal` and `-shm` if present) and start it
  again, with the same `SECRET_KEY` in the `.env`. Preview images and 3D previews are
  rebuilt automatically.
- The database has a schema version and is upgraded automatically on start.
- New and changed files are detected regularly, also on network shares (SMB/NFS). Keep
  the data folder on a local disk, though, because SQLite is unreliable on network
  shares.
