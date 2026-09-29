# Installation

[English](../en/installation.md) · **Deutsch** · [← Dokumentation](README.md)

## Voraussetzungen

Du brauchst Docker mit Docker Compose 2.24 oder neuer. Das Image gibt es für PCs und
Server (amd64) und für 64-Bit-ARM (arm64, z. B. Raspberry Pi 4/5 mit 64-Bit-System und
viele NAS). Docker wählt automatisch das passende.

## Schnellstart

```bash
mkdir plateshelf && cd plateshelf
curl -O https://raw.githubusercontent.com/schnuecks/plateshelf/main/compose.yaml
curl -o .env https://raw.githubusercontent.com/schnuecks/plateshelf/main/.env.example
mkdir -p library data
docker compose up -d
```

Danach ist PlateShelf unter `http://<server>:8000` erreichbar. Beim ersten Aufruf legst
du das Admin-Konto an; den Einrichtungscode dafür findest du im Protokoll (`docker compose
logs plateshelf`), siehe [Zugriffsschutz](configuration.md#zugriffsschutz). Lege deine
`.gcode.3mf`-, `.gcode`- oder `.bgcode`-Dateien in den Ordner `library`. Sie erscheinen nach spätestens zwei
Minuten, sofort über „Bibliothek neu einlesen“ in der Übersicht.

Die Ordner `library` und `data` müssen für den Benutzer beschreibbar sein, unter dem
der Container läuft (`PUID` und `PGID` in der `.env`, Standard 1000). Sonst bricht der
Start mit dem Fehler „unable to open database file“ ab.

## Aktualisieren

```bash
docker compose pull && docker compose up -d
```

Die Datenbank wird beim Start bei Bedarf automatisch auf den neuen Stand gebracht.
Wenn sich das Format der 3D-Vorschau ändert, werden die Vorschauen danach einmal im
Hintergrund neu erzeugt. Was sich in einer Version geändert hat, steht im
[CHANGELOG](../../CHANGELOG.md).
Kommst du von einer Version vor 0.7 (auch unter dem alten Namen *Druckarchiv*), aktualisiere
zuerst auf 0.7.7 und starte diese einmal.

## Hinter einem Reverse-Proxy

PlateShelf lauscht im Container auf Port 8000 und braucht keine besonderen
Einstellungen am Proxy. Ein Beispiel für Traefik (statt `ports:` in der
`compose.yaml`):

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.plateshelf.rule=Host(`plateshelf.example.com`)
      - traefik.http.services.plateshelf.loadbalancer.server.port=8000
```

## Daten und Sicherung

- Die Dateien in der Bibliothek werden nie verändert, nur beim Löschen in den
  Papierkorb verschoben.
- Datenbank (SQLite), Vorschaubilder, 3D-Vorschauen und deine Notizen liegen im
  Datenordner.
- **Sicherung:** „Einstellungen“ → „Sicherung“ lädt die Datenbank als eine Datei herunter,
  auch während PlateShelf läuft in sich stimmig. Sie enthält Projekte mit Notizen, Tags
  und Status, Zuordnungen, Einstellungen, Drucker, Benutzer und den Druckverlauf. Den
  Bibliotheksordner mit den Druckdateien sicherst du wie gewohnt. Heb auch den
  `SECRET_KEY` gut auf, denn nur mit ihm lassen sich die Zugangsdaten der Drucker lesen.
- **Automatische Sicherungen:** Einmal am Tag legt PlateShelf so eine Sicherung unter
  `backups/` im Datenordner ab und behält die letzten `BACKUP_KEEP` (Standard 7; `0`
  schaltet das ab). Die Einstellungen listen sie zum Herunterladen oder Zurückspielen.
- **Zurückspielen:** Unter „Einstellungen“ → „Sicherung“ lädst du eine Sicherung hoch oder
  wählst eine der automatischen. PlateShelf sichert vorher den aktuellen Stand als „vor dem
  Zurückspielen“, tauscht dann die Datenbank aus, bringt sie bei Bedarf auf den neuen Stand
  und erzeugt Vorschaubilder und 3D-Vorschauen neu. Benutzer und Anmeldungen kommen aus der
  Sicherung, eventuell musst du dich also neu anmelden.
- **Umzug auf einen anderen Rechner:** Bibliotheksordner kopieren, dort die Sicherung
  zurückspielen und denselben `SECRET_KEY` in der `.env` verwenden. Von Hand, ohne
  Oberfläche: PlateShelf stoppen, die Sicherung als `plateshelf.sqlite` in den Datenordner
  legen (`plateshelf.sqlite-wal` und `-shm` löschen, falls vorhanden) und wieder starten.
- Die Datenbank hat eine Schema-Version und wird beim Start automatisch aktualisiert.
- Neue und geänderte Dateien werden regelmäßig erkannt, auch auf Netzwerkfreigaben
  (SMB/NFS). Den Datenordner solltest du dagegen lokal ablegen, weil SQLite auf
  Netzwerkfreigaben unzuverlässig ist.
