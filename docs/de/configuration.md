# Konfiguration

[English](../en/configuration.md) · **Deutsch** · [← Dokumentation](README.md)

## Einstellungen

Alle Einstellungen stehen in der `.env`. Die `compose.yaml` reicht sie an den Container
weiter.

**Container und Ordner**

| Variable | Standard | Bedeutung |
|---|---|---|
| `PLATESHELF_IMAGE` | `ghcr.io/schnuecks/plateshelf:latest` | Verwendetes Image |
| `PUID`, `PGID` | 1000 | Benutzer und Gruppe, unter denen der Container läuft |
| `TZ` | Europe/Berlin | Zeitzone |
| `PORT` | 8000 | Port auf dem Host |
| `LIBRARY` | `./library` | Ordner mit den `.gcode.3mf`-, `.gcode`- und `.bgcode`-Dateien |
| `DATA` | `./data` | Ordner für Datenbank, Vorschaubilder und 3D-Vorschauen |
| `LOG_LEVEL` | INFO | Ausführlichkeit der Protokolle (`docker compose logs`): `DEBUG`, `INFO` oder `WARNING` |

**Bibliothek**

| Variable | Standard | Bedeutung |
|---|---|---|
| `SCAN_INTERVAL` | 120 | Sekunden zwischen zwei Durchläufen durch die Bibliothek |
| `MAX_UPLOAD_MB` | 1024 | Größenlimit pro hochgeladener Datei in MB |
| `PREVIEW_PREBUILD` | true | 3D-Vorschauen nach dem Einlesen im Hintergrund vorbereiten |
| `TRASH_DAYS` | 30 | Tage bis zum endgültigen Löschen aus dem Papierkorb (0 = nie) |

**Direktimport**

| Variable | Standard | Bedeutung |
|---|---|---|
| `PRINTER_<n>_NAME`, `_IP`, `_SERIAL`, `_CODE` | – | Bambu-Drucker für den Direktimport (optional) |
| `PRINTER_<n>_TYPE` | `bambu` | `prusalink` für Prusa, `moonraker` für Klipper-Drucker |
| `PRINTER_<n>_PASSWORD` | – | PrusaLink-Passwort (Prusa) |
| `PRINTER_<n>_API_KEY` | – | Moonraker-API-Schlüssel, nur wenn nötig (Klipper) |
| `PRINTER_SYNC_INTERVAL` | 600 | Sekunden zwischen zwei Abfragen der Drucker |
| `PRINTER_IMPORT_DIR` | From printer | Zielordner für importierte Drucke in der Bibliothek |
| `PRINT_HISTORY` | – | `true` oder `false` legt den Druckverlauf fest; ohne Angabe gilt der Schalter in den Einstellungen (Standard an) |
| `PRINT_HISTORY_INTERVAL` | 60 | Sekunden zwischen zwei Abfragen von Klipper- und Prusa-Druckern |

**Oberfläche**

| Variable | Standard | Bedeutung |
|---|---|---|
| `DEFAULT_LANGUAGE` | – | Voreingestellte Sprache: `de`, `en`, `fr`, `es`, `it` oder `nl`; leer: Sprache des Browsers |
| `DEFAULT_THEME` | system | Voreingestelltes Design: `system`, `light` oder `dark` |

**Zugriffsschutz**

| Variable | Standard | Bedeutung |
|---|---|---|
| `AUTH_USER`, `AUTH_PASSWORD_HASH` | – | Anmeldung mit Passwort-Hash einschalten |
| `AUTH_PASSWORD` | – | Statt des Hashs: Passwort im Klartext |
| `SESSION_DAYS` | 30 | Wie lange eine Anmeldung im Browser gilt |
| `SECRET_KEY` | – | Schlüssel zum Verschlüsseln der Druckerzugänge und für die Anmeldung; dringend empfohlen, siehe [Geheimnisse](#geheimnisse) |
| `TRUSTED_ORIGINS` | – | Weitere Adressen, die Änderungen schicken dürfen, durch Komma getrennt |

## Zugriffsschutz

Ohne weitere Einstellungen hat PlateShelf keine Anmeldung: Jeder, der es erreicht,
kann Dateien hochladen und löschen. Im Heimnetz ist das meist in Ordnung. Für mehr
Schutz gibt es zwei Wege, die sich auch kombinieren lassen.

**Eigene Anmeldung:** Setze `AUTH_USER` und ein Passwort in der `.env`. Das Passwort
legst du am besten als Hash ab statt im Klartext:

```bash
docker compose exec plateshelf python -m app.auth hash
```

Der Befehl fragt das Passwort zweimal ab und gibt eine Zeile wie
`AUTH_PASSWORD_HASH=scrypt:15:8:1:…` aus, die neben `AUTH_USER` in die `.env` kommt.
`AUTH_PASSWORD=…` im Klartext funktioniert weiterhin.

- Der Browser zeigt dann eine Anmeldeseite. Die Anmeldung gilt `SESSION_DAYS` Tage,
  oben rechts gibt es „Abmelden“.
- Nach fünf Fehlversuchen ist eine Adresse für fünf Minuten gesperrt.
- Ändert sich das Passwort, sind alle bestehenden Anmeldungen ungültig.
- Skripte und Dashboards melden sich mit denselben Zugangsdaten per HTTP Basic Auth an.
- `/healthz` bleibt für den Docker-Healthcheck erreichbar, meldet ohne Anmeldung aber
  nur, dass PlateShelf läuft.

**Anmeldung am Proxy**, z. B. mit Authelia oder Basic Auth. Das empfiehlt sich
besonders, wenn PlateShelf aus dem Internet erreichbar ist.

**Schutz vor fremden Webseiten:** Unabhängig davon nimmt PlateShelf Änderungen
(Hochladen, Löschen, Einstellungen …) nur von seinen eigenen Seiten an. Formulare, die
eine fremde Webseite im Hintergrund an PlateShelf schickt, werden abgelehnt.
Maßgeblich ist die Adresse, die der Browser mitsendet; hinter einem Proxy zählt der
weitergereichte Host (`X-Forwarded-Host`). Skripte ohne Browser (z. B. `curl`) sind
nicht betroffen. Sollen weitere Seiten Änderungen schicken dürfen, trage sie in
`TRUSTED_ORIGINS` ein, etwa `TRUSTED_ORIGINS=https://dashboard.example.com`.

## Geheimnisse

PlateShelf braucht die Zugangscodes, Passwörter und API-Schlüssel deiner Drucker lesbar,
um sich mit ihnen zu verbinden. Sie werden deshalb nicht gehasht, sondern verschlüsselt
(AES-256-GCM) in der Datenbank gespeichert. Den Schlüssel leitet PlateShelf aus
`SECRET_KEY` ab, und den nimmt es nur aus der Umgebung, nie aus dem Datenordner. Eine
Kopie der Datenbank allein, etwa aus einer Sicherung, nützt also niemandem.

```bash
docker compose exec plateshelf python -m app.auth secret
```

gibt einen zufälligen `SECRET_KEY=…` aus; trag ihn in die `.env` ein und starte
PlateShelf neu. Vorhandene Zugangsdaten werden dann automatisch verschlüsselt. Heb den
Schlüssel gut auf: Ohne ihn lassen sich die gespeicherten Zugangsdaten nicht mehr lesen,
und du müsstest sie neu eingeben. Ohne `SECRET_KEY` läuft PlateShelf weiter, speichert
Zugangsdaten aber unverschlüsselt, und Anmeldungen gelten nur bis zum nächsten Neustart;
die Einstellungsseite weist darauf hin.

**Geheimnisse als Dateien (Docker-Secrets):** Jede sensible Variable lässt sich mit der
Endung `_FILE` auch aus einer Datei lesen: `SECRET_KEY_FILE`, `AUTH_PASSWORD_HASH_FILE`,
`AUTH_PASSWORD_FILE` sowie `PRINTER_<n>_CODE_FILE`, `…_PASSWORD_FILE`, `…_API_KEY_FILE`.
So stehen sie weder in der `.env` noch in `docker inspect`:

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
