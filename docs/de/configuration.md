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
| `AUTH` | an | `off` schaltet die Anmeldung aus, etwa wenn schon der Reverse-Proxy anmeldet |
| `SESSION_DAYS` | 30 | Wie lange eine Anmeldung im Browser gilt |
| `AUTH_USER`, `AUTH_PASSWORD_HASH` | – | Nur für Updates von Versionen ohne Benutzerkonten: werden einmalig zum ersten Admin-Konto |
| `SECRET_KEY` | – | Schlüssel zum Verschlüsseln der Druckerzugänge; dringend empfohlen, siehe [Geheimnisse](#geheimnisse) |
| `TRUSTED_ORIGINS` | – | Weitere Adressen, die Änderungen schicken dürfen, durch Komma getrennt |

## Zugriffsschutz

PlateShelf verlangt eine Anmeldung. Konten, Passwörter und Rollen verwaltest du in
PlateShelf selbst; in die `.env` muss dafür nichts.

**Erster Start:** Solange es kein Konto gibt, führt jede Seite zu „Admin-Konto anlegen“.
Die Seite fragt nach einem Einrichtungscode, den PlateShelf ins Protokoll des Containers
schreibt. So kann niemand anderes in deinem Netz das Konto vor dir anlegen:

```bash
docker compose logs plateshelf | grep "Einrichtungscode"
```

Alternativ gibt `docker compose exec plateshelf python -m app.auth setup-code` ihn aus.
Code, Benutzername und Passwort (mindestens 8 Zeichen) eintragen, und du bist als Admin
angemeldet.

**Update von einer älteren Version:** Sind `AUTH_USER` und `AUTH_PASSWORD_HASH` (oder
`AUTH_PASSWORD`) gesetzt, macht PlateShelf daraus beim Start das erste Admin-Konto. Du
meldest dich an wie bisher; danach können die beiden Zeilen aus der `.env`, und das
Passwort änderst du in PlateShelf. Dashboards, die sich damit per Basic Auth angemeldet
haben, funktionieren weiter.

**Rollen:** Unter „Einstellungen“ → „Benutzer“ legen Admins Konten an und wählen eine Rolle:

| Rolle | Darf |
|---|---|
| Admin | alles, auch Einstellungen, Benutzer und API-Tokens |
| Benutzer | Dateien und Projekte hochladen, bearbeiten, verschieben und löschen, Papierkorb leeren; keine Einstellungen |
| Nur lesen | ansehen und herunterladen, Sprache und Design wählen, eigenes Passwort ändern |

Mindestens ein Admin bleibt immer erhalten. Admins können jedem Konto ein neues Passwort
geben; das meldet das Konto auf allen Geräten ab.

**Eigenes Konto:** Das Personen-Symbol oben rechts öffnet „Mein Konto“:

- Passwort ändern; alle anderen Geräte werden dabei abgemeldet
- sehen, wo du angemeldet bist (Browser und System, seit wann, zuletzt aktiv), und einzelne
  oder alle anderen Geräte abmelden

**Sitzungen:** Eine Anmeldung gilt `SESSION_DAYS` Tage. Der Browser bekommt nur einen
Zufallswert, in der Datenbank steht nur dessen Hash. Abmelden beendet die Sitzung sofort
auf dem Server, ein kopiertes Cookie nützt danach nichts mehr. Sitzungen überstehen
Neustarts, mit oder ohne `SECRET_KEY`.

**Schutz vor Durchprobieren:** Nach fünf Fehlversuchen (Passwort oder Einrichtungscode)
ist eine Adresse für fünf Minuten gesperrt. Ob es einen Benutzernamen gibt, verrät weder
die Antwort noch ihre Dauer.

**Skripte** melden sich mit Benutzername und Passwort per HTTP Basic Auth an; die Rolle
gilt auch hier. Für Dashboards sind [API-Tokens](dashboard.md#api-tokens) die bessere Wahl.

**Ausgesperrt?** Neues Passwort über die Konsole setzen; gibt es das Konto nicht, entsteht
ein Admin-Konto mit diesem Namen:

```bash
docker compose exec plateshelf python -m app.auth reset <name>
```

**Ohne Anmeldung:** `AUTH=off` in der `.env` schaltet all das ab; dann darf jeder, der
PlateShelf erreicht, alles. Das ist nur sinnvoll, wenn schon der Reverse-Proxy anmeldet
(etwa Authelia oder Authentik) oder niemand anderes PlateShelf erreicht.

`/healthz` bleibt für den Docker-Healthcheck erreichbar, meldet ohne Anmeldung aber nur,
dass PlateShelf läuft.

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
die Zugangsdaten der Drucker aber unverschlüsselt; die Einstellungsseite weist darauf hin.
Passwörter der Benutzer liegen immer als scrypt-Hash vor und brauchen den Schlüssel nicht.

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
