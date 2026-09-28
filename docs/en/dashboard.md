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
- With sign-in (the default), add an API token to the widget, see below.
- If Homepage and PlateShelf share a Docker network, use the internal address for
  `url`, e.g. `http://plateshelf:8000/api/stats`. This also works if PlateShelf sits
  behind a sign-in at the proxy from the outside.

## API tokens

Dashboards should not know your password. Under “Settings” → “API tokens”, an admin
creates a token with a name such as “Homepage”. It is shown only once; copy it right away.
PlateShelf stores only its hash and shows the last four characters to tell tokens apart.

- A token can only read, and only below `/api/` (e.g. `/api/stats`, `/api/printing`).
- It is sent as `Authorization: Bearer <token>` or `X-API-Key: <token>`.
- The list shows when each token was last used. “Revoke” makes it invalid at once.

For Homepage, add the header to the widget:

```yaml
    widget:
      type: customapi
      url: http://<server>:8000/api/stats
      headers:
        Authorization: Bearer ps_…
```

Instead of a token, a script can also sign in with user name and password via HTTP Basic
Auth (`username` and `password` in the widget); a “Read only” account is enough for that.
