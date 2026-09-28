# Dashboard-Widget

[English](../en/dashboard.md) · **Deutsch** · [← Dokumentation](README.md)

Unter `/api/stats` liefert PlateShelf seine Kennzahlen als JSON:

| Feld | Bedeutung |
|---|---|
| `projects`, `files`, `plates` | Anzahl Projekte, Dateien und geslicte Platten |
| `print_time_h`, `print_time_s` | Druckzeit aller Projekte in Stunden bzw. Sekunden |
| `filament_kg`, `filament_g` | Filamentverbrauch |
| `cost_eur`, `energy_kwh` | Kosten (Material und Strom) und Stromverbrauch |
| `new_files_7d`, `last_added` | Neue Dateien der letzten 7 Tage, Zeitpunkt der neuesten |
| `printers_ok`, `printers_total`, `last_printer_sync` | Erreichbare und eingerichtete Drucker, letzter Abruf |
| `prints_30d`, `prints_failed_30d` | Fertige und abgebrochene oder fehlgeschlagene Drucke der letzten 30 Tage |
| `printing_now`, `last_print_end` | Gerade laufende Drucke, Ende des letzten Drucks |
| `printing_name`, `printing_printer`, `printing_progress`, `printing_remaining_min` | Der laufende Druck: Projekt, Drucker, Fortschritt in %, Restminuten |
| `trash_items`, `version` | Einträge im Papierkorb, Version von PlateShelf |

Für [Homepage](https://gethomepage.dev) genügt das eingebaute Widget `customapi` in der
`services.yaml`. Eine Kachel zeigt bis zu vier Felder:

```yaml
- PlateShelf:
    icon: mdi-printer-3d
    href: http://<server>:8000
    description: 3D-Drucke
    widget:
      type: customapi
      url: http://<server>:8000/api/stats
      refreshInterval: 60000
      mappings:
        - field: projects
          label: Projekte
          format: number
        - field: print_time_h
          label: Druckzeit
          format: float
          suffix: h
        - field: filament_kg
          label: Filament
          format: float
          suffix: kg
        - field: cost_eur
          label: Kosten
          format: float
          suffix: €
```

- Statt der Kosten passen auch `new_files_7d` („Neu (7 Tage)“) oder `printers_ok`
  („Drucker online“).
- Mit Anmeldung (Standard) trägst du im Widget ein API-Token ein, siehe unten.
- Laufen Homepage und PlateShelf im selben Docker-Netz, nimm bei `url` die interne
  Adresse, z. B. `http://plateshelf:8000/api/stats`. Das funktioniert auch, wenn das
  PlateShelf nach außen hinter einer Anmeldung am Proxy liegt.

## API-Tokens

Dashboards sollen dein Passwort nicht kennen. Unter „Einstellungen“ → „API-Tokens“ legt
ein Admin ein Token mit einem Namen wie „Homepage“ an. Es wird nur einmal angezeigt, also
gleich kopieren. PlateShelf speichert nur seinen Hash und zeigt die letzten vier Zeichen,
damit du Tokens auseinanderhalten kannst.

- Ein Token darf nur lesen, und nur unter `/api/` (z. B. `/api/stats`, `/api/printing`).
- Es wird als `Authorization: Bearer <Token>` oder `X-API-Key: <Token>` mitgeschickt.
- Die Liste zeigt, wann ein Token zuletzt benutzt wurde. „Widerrufen“ macht es sofort ungültig.

Für Homepage kommt die Kopfzeile ins Widget:

```yaml
    widget:
      type: customapi
      url: http://<server>:8000/api/stats
      headers:
        Authorization: Bearer ps_…
```

Statt eines Tokens kann sich ein Skript auch mit Benutzername und Passwort per HTTP Basic
Auth anmelden (`username` und `password` im Widget); dafür genügt ein Konto „Nur lesen“.
