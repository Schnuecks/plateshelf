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
- Ist die eigene Anmeldung eingeschaltet, trage im Widget zusätzlich `username` und
  `password` ein, also denselben Benutzernamen und dasselbe Passwort wie für die Anmeldung.
- Laufen Homepage und PlateShelf im selben Docker-Netz, nimm bei `url` die interne
  Adresse, z. B. `http://plateshelf:8000/api/stats`. Das funktioniert auch, wenn das
  PlateShelf nach außen hinter einer Anmeldung am Proxy liegt.
