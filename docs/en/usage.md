# Usage

**English** · [Deutsch](../de/usage.md) · [← Documentation](README.md)

## Library and projects

**Sliced files** work best, because only they contain print time, weight and filament
usage:

- **`.gcode.3mf`** from Bambu Studio or OrcaSlicer, via “Export → Export plate sliced
  file” or “Export all sliced file”. One file can contain several plates.
- **`.gcode`** from PrusaSlicer, SuperSlicer, OrcaSlicer, Cura, Creality Print and other
  slicers. One file is one plate. PlateShelf reads print time, filament usage, colours,
  costs, the printer and the key settings from the comments the slicer writes into the
  file, as well as the embedded preview image. Cura only stores the filament length; the
  weight is then estimated for PLA with 1.75 mm.
- **`.bgcode`** (binary G-code) from PrusaSlicer, the default for Prusa MK4, XL, Mini and
  Core One. It contains the same information in compact form, including the preview
  images.

This is how projects are formed:

- **Each subfolder** of the library is a project.
- **Files in the main folder** are grouped by name: `Benchy_plate_1.gcode.3mf` and
  `Benchy_plate_2.gcode.3mf` both end up in the project “Benchy”. Suffixes such as
  `_plate_3`, ` (2)`, `_v2`, `_gcode` or `.stl` are ignored.
- **Meaningless names** such as `(Unsaved)` or random IDs are replaced by the title
  stored in the file, if there is one.
- On the project page, every file can be **assigned to another project by hand**. This
  assignment survives a rescan.

If you export plates individually, each file also contains the preview images of all
other plates. If a plate is available sliced in another file of the project, its
preview is not shown twice.

## Uploading

Use “Upload” in the top right to upload one or more files by drag & drop, optionally
straight into a project folder. Existing files are never overwritten: identical files
are skipped, and files with the same name but different content get “(2)” appended.

## 3D preview

A sliced file no longer contains a 3D model but the finished G-code. The 3D preview
therefore shows the actual toolpaths, similar to your slicer. Line types are recognised
for Bambu Studio, OrcaSlicer, PrusaSlicer and Cura.

**Colouring**

- by filament, by line type (outer wall, infill, supports and so on) or in a single
  colour of your choice, handy for light filaments
- by measurement, like in the slicer: speed (mm/s), volumetric flow (mm³/s), part
  cooling fan (%) and nozzle temperature (°C), on a scale from blue (low) to red (high).
  Single outliers don’t distort the scale.
- Individual filaments and line types can be hidden.
- Travel moves, retractions and seams (start of each outer wall) can be shown, each with
  a count. This helps to explain stringing or an uneven seam.

**Layers and playback**

- Layer slider with height display, also via the up and down arrow keys
- “This layer only” shows a single layer.
- Playback lets the print head trace the toolpaths layer by layer (▶ or space bar,
  speed 1×, 10× or 50×). “Position in layer” moves to any point of a layer by hand.
- The left and right arrow keys switch between the plates of a project.

**View**

- Rotate with the left mouse button, pan with the right, zoom towards the cursor with
  the mouse wheel; on touch devices with one or two fingers
- Double-click or double-tap resets the view; the camera position is remembered per plate.
- Full screen (also key F) and “Save image” as PNG
- The model stands on a build plate with 10 and 50 mm lines. Seen from below, the plate
  is hidden so the first layer stays visible.
- Ambient occlusion darkens the grooves between lines and layers and makes the model look
  more three-dimensional. It is only active while the view is still, switches off
  automatically on slow devices and can be turned off by hand.

**Performance:** After a file is read, its G-code is processed once in the background
and cached in the data folder. For very large prints (100 MB of G-code) this takes about
10 to 20 seconds; after that the preview opens instantly. When opening, the preview
briefly measures the device and, while rotating, automatically picks the best-looking
display that still runs smoothly. No internet connection is required.

## Costs

The costs shown consist of material and electricity:

- **Material:** usage per filament times the price from the filament profile in the
  slicer (e.g. “Price” in Bambu Studio, “Cost” in PrusaSlicer). If no price is set
  there, this part is missing. Cura does not store a price.
- **Electricity:** print time times the printer’s average power times the electricity
  price. You set price and power under “Settings”, per printer if you like. Defaults are
  €0.35 per kWh and 100 watts.

The most accurate way to measure average power is a smart plug with energy metering:
energy of one print in Wh divided by the print time in hours. Wear and failed prints are
not included.

## Trash

You delete files or entire projects on the project page. They are not removed right
away but moved, with their original path, into the hidden `.trash/` folder inside the
library. Right after deleting, you can take it back with “Undo”.

“Trash” in the top right lists all deleted files and projects:

- **Restore** puts the files back where they were. Project name, notes, tags, status,
  source and manual assignments come back too. If another file with the same name is
  there by now, the restored one is saved as “(2)”. Nothing is ever overwritten.
- **Delete permanently** removes one item, **Empty trash** removes all of them.
- Items are deleted permanently after `TRASH_DAYS` days (default 30). With
  `TRASH_DAYS=0` they stay until you delete them yourself.

## Language and theme

In the top right of the title bar you choose the language (globe with a code such as
“EN”) and the theme: clicking the icon switches between system, light and dark. Both
apply per browser, so everyone in the household can have their own setting. Numbers,
amounts and dates follow the selected language.

Without a choice, `DEFAULT_LANGUAGE` and `DEFAULT_THEME` from the `.env` apply. If no
language is set there either, the browser language is used, and English if that isn’t
one of the six.
