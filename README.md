# Cable Explode

QGIS plugin that draws stacked cables a short distance apart so each one is visible. It changes the symbols on the map only. Stored geometries stay where they are.

It runs on Windows and macOS with the Python and Qt libraries that ship with QGIS. No extra Python packages are required.

QGIS 3.34 or newer is required. Developed against QGIS 3.44.

## Install

The plugin folder has to stay named `cable_explode`, with `metadata.txt` directly inside it. QGIS loads the plugin from that package name. Close QGIS before copying the folder in, then start QGIS and enable **Cable Explode** under **Plugins → Manage and Install Plugins**.

If you use a named QGIS profile instead of the default profile, copy the folder into that profile's `python/plugins` directory.

Clone this repository under that name:

```bash
git clone https://github.com/tsmkirby/cabex.git cable_explode
```

Copy the folder rather than making a shortcut. QGIS reads the plugin from that directory.

### Windows

1. Copy the `cable_explode` folder to `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`.
2. Paste that path into File Explorer's address bar. It is usually `C:\Users\<your name>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins`.
3. Confirm the plugins folder contains `cable_explode\metadata.txt`.
4. Start QGIS and enable the plugin.

### macOS

1. Copy the `cable_explode` folder into `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`.
2. Confirm that folder contains `metadata.txt`.
3. Start QGIS and enable the plugin.

## Toolbar

The **Cable Explode** toolbar has four actions.

| Action | What it does |
| --- | --- |
| Explode cables | Draws stacked cables apart. Click again to put the symbols back. |
| Explode settings | Sets the separation, the minimum shared length, and which line layers take part. |
| Select exploded cables | Selects cables where they are drawn, so one cable can be picked out of a stack. |
| Identify exploded cables | Lists every cable drawn at the clicked spot, with its attributes, in the **Exploded Identify** panel. |

Until layers are chosen in **Explode settings**, every line layer that looks like a cable is included. A cable layer has one of these fields: `cable_id`, `strand_count`, `fiber_count`, or `media_type`. The settings list can also include any other line layer.

## What moves

A piece of a cable moves only when it lies on top of another cable.

- The two segments follow the same path, within 0.1 m.
- They share that path for at least the minimum length. The default is 1 m.
- Neighbors in that stack are drawn one full separation apart. The default separation is 0.2 m, so two stacked cables sit 0.1 m to either side of the real path. A larger stack is wider than the separation.
- The stack stays centered on the real path.
- The rest of the line, including the ends outside the shared run, stays on the stored coordinates.
- A cable that ends inside a stack bends back to its stored end over a short lead-in, three times its sideways shift and never more than a third of its length. A cable split at a splice enclosure, or ending at a pole or device, is drawn entering it, while cables that pass through keep their lanes.

Crossings, short shared stubs, and cables that only pass nearby stay put. Turning the tool off restores the original symbols.

Labels, snapping, and the built-in QGIS Select and Identify tools keep using the stored geometry, which lies under the whole stack. Use the two exploded tools instead. A selected cable is highlighted on the exploded line so you can see which one is selected.

## Selecting and identifying exploded cables

Both tools search the layers chosen in **Explode settings**, using the lines as they are drawn. With explode off they use the stored lines. Hidden layers, layers outside their scale range, and features the layer style does not draw are skipped, as in QGIS.

**Select exploded cables**

- Click a cable to select it. Selections on the other exploded layers are cleared.
- Click the same stack again to select the next cable in it. The status bar shows which cable of the stack is selected, so a stack can be worked through even when it is drawn too close to click each line.
- Shift-click or Ctrl-click adds or removes one cable.
- Drag a box to select every cable whose drawn line touches it. Shift adds, Ctrl removes, and Ctrl+Shift keeps only cables that are already selected and inside the box.
- Click an empty spot to clear the selection.

The selection is the normal layer selection, so the attribute table, **Zoom to selection**, and editing tools work on it.

**Identify exploded cables**

Click a spot to list each cable drawn there, nearest first. Choose a cable in the panel to highlight its drawn line. **Select** makes it the selected feature on its layer, **Zoom to** zooms to it, and **Open form** opens its attribute form. The form can be edited when the layer is in edit mode. Double-clicking an entry also opens the form.

Saving the project writes the original layer style. The offset is a view aid and is not stored in the project.

## Tests

The offset math has no QGIS imports. From the directory that contains the `cable_explode` folder:

```bash
python3 -m unittest cable_explode.tests.test_offset
```
