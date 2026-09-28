# Dashboard widget

**English** · [Deutsch](../de/dashboard.md) · [← Documentation](README.md)

At `/api/stats`, PlateShelf provides its key figures as JSON:

| Field | Meaning |
|---|---|
| `projects`, `files`, `plates` | Number of projects, files and sliced plates |
| `print_time_h`, `print_time_s` | Print time of all projects in hours or seconds |
| `filament_kg`, `filament_g` | Filament usage |
| `cost_eur`, `energy_kwh` | Costs (material and electricity) and energy use |
| `new_files_7d`, `last_added` | New files in the last 7 days, time of the newest |
| `printers_ok`, `printers_total`, `last_printer_sync` | Reachable and configured printers, last query |
| `prints_30d`, `prints_failed_30d` | Finished and cancelled or failed prints in the last 30 days |
| `printing_now`, `last_print_end` | Prints running right now, end of the last print |
| `printing_name`, `printing_printer`, `printing_progress`, `printing_remaining_min` | The running print: project, printer, progress in %, remaining minutes |
| `trash_items`, `version` | Items in the trash, PlateShelf version |

For [Homepage](https://gethomepage.dev), the built-in `customapi` widget in your
`services.yaml` is all you need. A tile shows up to four fields:

```yaml
- PlateShelf:
    icon: mdi-printer-3d
    href: http://<server>:8000
    description: 3D prints
    widget:
      type: customapi
      url: http://<server>:8000/api/stats
      refreshInterval: 60000
      mappings:
        - field: projects
          label: Projects
          format: number
        - field: print_time_h
          label: Print time
          format: float
          suffix: h
        - field: filament_kg
          label: Filament
          format: float
          suffix: kg
        - field: cost_eur
          label: Cost
          format: float
          suffix: €
```

- Instead of the costs, `new_files_7d` (“New (7 days)”) or `printers_ok` (“Printers
  online”) work well too.
- If the built-in sign-in is on, also add `username` and `password` to the widget, with
  the same user name and password as for the sign-in.
- If Homepage and PlateShelf share a Docker network, use the internal address for
  `url`, e.g. `http://plateshelf:8000/api/stats`. This also works if PlateShelf sits
  behind a sign-in at the proxy from the outside.
