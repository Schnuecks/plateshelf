# Printers and print history

**English** · [Deutsch](../de/printers.md) · [← Documentation](README.md)

![Settings with printers of three types](../screenshots/settings.png)

## Direct import from printers

PlateShelf can fetch sliced prints from your printers itself:

- **Bambu Lab** printers via FTPS from the SD card, using the printer’s access code.
  LAN-only mode is not required as long as the firmware allows file access.
- **Prusa printers** via PrusaLink (MK4, MK3.9, XL, Mini, Core One). PlateShelf fetches
  the `.bgcode` and `.gcode` files from the USB drive or internal storage.
- **Klipper printers** via [Moonraker](https://moonraker.readthedocs.io), the interface
  behind Mainsail and Fluidd. This covers for example Creality K1 and K2, Sovol SV08,
  Qidi, Elegoo Neptune 4 and Voron. PlateShelf fetches the `.gcode` files from the
  printer’s G-code folder.

**Set up in the interface:** Add your printers under “Settings” → “Printers for direct
import” and pick the type. Instead of the IP address you can also enter a hostname such as
`printer.local` or `mini.fritz.box`, as long as PlateShelf can resolve it (inside the Docker
container this does not always work for `.local` names).

- *Bambu Lab:* name, IP address or hostname, serial number and access code. You find these in the
  network settings on the printer.
- *Prusa (PrusaLink):* name, IP address or hostname and the PrusaLink password. You find it on the
  printer under Settings → Network → PrusaLink; the user name is `maker`. Older firmware
  that uses an API key instead: enter the key as password.
- *Klipper (Moonraker):* name and address of the printer. Without a port, PlateShelf
  uses Moonraker’s default port 7125; you can also enter a full address such as
  `http://192.0.2.21:7125`. An API key is only needed if Moonraker does not list the
  PlateShelf host under `trusted_clients`. You find the key in Mainsail or Fluidd, or
  via `http://<printer>:7125/access/api_key` from a trusted device.

The dot in front of each printer in the settings shows every few seconds whether it is
reachable. When a printer comes back, PlateShelf fetches its files right away.


Access codes and API keys are stored but never shown again; leave the field empty when
editing to keep them. After saving, the printers are queried right away and the result
is shown next to each printer.

**Set up via `.env`:** Alternatively, define the printers numbered from 1:

```bash
PRINTER_1_NAME=P2S
PRINTER_1_IP=192.0.2.20
PRINTER_1_SERIAL=<serial number>
PRINTER_1_CODE=<access code>
PRINTER_2_TYPE=moonraker
PRINTER_2_NAME=K1
PRINTER_2_IP=192.0.2.21
PRINTER_2_API_KEY=<optional>
PRINTER_3_TYPE=prusalink
PRINTER_3_NAME=MK4S
PRINTER_3_IP=192.0.2.22
PRINTER_3_PASSWORD=<PrusaLink password>
# …
```

`PRINTER_<n>_TYPE` is `bambu` (default), `prusalink` or `moonraker`. Printers set up this way appear
read-only in the settings. If a printer is set in both places, the `.env` wins.

**How it works:**

- Every `PRINTER_SYNC_INTERVAL` seconds (default 600), or immediately via “Fetch from
  printers” in the overview, PlateShelf lists the files on the printer.
- New prints are stored under `From printer/<printer name>/` in the library and grouped
  into projects by name.
- The printer always saves a project under the same name and overwrites it when a
  different plate is printed. PlateShelf therefore stores each printed plate as a file
  of its own (`…_plate_2.gcode.3mf`), so nothing gets lost. Prusa and Klipper printers
  keep their file names; a changed file replaces the previous copy.
- Each file is fetched only once. What you delete in the archive is not imported again.
- Files on the printer are only read, never changed or deleted.
- If a printer is switched off or in standby, it is simply queried again next time. Only
  a wrong access code, password or API key shows up as a warning in the overview.

## Print history

For printers set up for the direct import, PlateShelf records every print: start, end,
duration, printer and outcome (finished, cancelled, failed). “History” in the top bar
lists all prints; the project page shows the prints of its files, and the overview shows
how often a project was printed. The overview can be sorted by “Recently printed” or
“Most printed” and filtered by “Printed before”, “Never printed” or “Partly printed”.

**Plates printed:** for projects with several plates, the overview shows how many of them
are printed (“3/4 plates printed”), and the project page marks each printed plate with a
check mark and “Printed (2×)”. If every plate is its own file, as with plates fetched from a
Bambu printer or G-code files, each print of that file counts; if a file holds several
plates, the plate the printer reported counts. “Partly printed” lists the projects where
plates are still missing.

A running print appears in the top bar with printer, project, progress and remaining
time, and on the project's card in the overview; the top bar refreshes every 30 seconds.
Bambu and Prusa report the remaining time themselves, for Klipper it is estimated from the
progress so far.

- **Bambu Lab:** PlateShelf keeps an MQTT connection to the printer (port 8883, same
  access code) and learns about each print right away.
- **Klipper:** Moonraker keeps its own history, which PlateShelf reads every minute.
- **Prusa:** PrusaLink only knows the current print; PlateShelf queries it every minute
  and records it.

A print is matched to the file fetched from the printer, and thus to its project. If the
file is not in the library yet, PlateShelf fetches it right away and links the print
afterwards. A finished print sets a project without status to “Printed”.

**Notes per print:** under “Note” in the history and on the project page, you rate a
print (👍 turned out well, 👎 failed) and write down what went well or wrong, for example
“warping at one corner, use a brim next time”. The project notes describe the model, these
notes a single print.

Test prints and the like can be removed from the history with “×”, on the history page
or the project page. They no longer count, and if such a print had set the project to
“Printed” and no other finished print remains, the status is reset.

The history can be switched off under “Settings” → “Print history”, e.g. if a Bambu
printer should not get an additional MQTT connection. `PRINT_HISTORY=true` or `false` in
the `.env` fixes the setting; the switch in the interface is then hidden.

**Statistics:** the “Statistics” tab on the history page shows the last twelve months:
finished prints, print time, filament and costs, prints per month (finished and cancelled
or failed), costs per month, filament by material and the success rate per printer.
Filament and costs only count finished prints.
