# Sculpt Kit
A Blender extension for sculptors: tuned clay and rake brushes, ZBrush-style navigation/keybinds, subtools, subdivision levels, a cleaner sculpting layout, and a side-by-side reference view. Every feature can be turned on or off.

<img width="1850" height="1047" alt="Screenshot 2026-10-22 at 7 41 46 PM" src="https://github.com/user-attachments/assets/db753586-4d60-4d77-994f-0786cc1f6e10" />



## Install

1. Download `sculpt_kit-<version>.zip` from [Releases](../../releases).
2. Drag .zip file onto the Blender window
3. Enable **Sculpt Kit** in **Edit > Preferences > Add-ons**, where feature toggles are also located

Disabling or uninstalling the kit restores Blender's theme and UI immediately.

Settings are in **Preferences > Add-ons > Sculpt Kit**, panels in the **Sculpt Kit** tab of the viewport sidebar (N), and brushes in the **Sculpt Kit** tab of the brush shelf.

## Features

- **fRemesher**: the best free remesher you can get. Has multiple tuning options.
- **Clay and Rake Brushes**: brushes I hand tuned and enjoy
- **Reference view:** compare your sculpt side by side with a reference, at the same view and scale
- **Optional Zbrush-like UI and key bindings:** a KlaySculpt or ZBrush color theme, layout, brush bar and materials
- **Spawn Sculpt-ready forms:** Sphere, cube, or cylinder made of quads
- **Subtools:** switch between, hide and organize the meshes in your scene
- **Subdivide:** quick access to Multires and subdivision while you sculpt

### fRemesher

A quad remesher for sculpts, in the Sculpt Kit tab's Remesh panel. It works out where edge loops should run
from the sculpt's curvature, builds clean quads along them, and snaps them back onto the sculpt, so a dense
sculpt or scan becomes a light cage you can subdivide and keep sculpting.

- **Faces:** about how many faces you get.
- **Adaptive Size:** quads smaller on thin parts and across tight curves, longer along gentle ones. Low face
  counts work best with more of it; with **Auto** on it follows Faces.
- **Detect Edges** keeps hard edges;
- **Follow Ridges** runs loops along ridges and
  valleys such as a nose, brow or lips.
- **Advanced:** mesh symmetry, cage fitting for subdivision shrinkage, rebuilding pinch points, and toggle smooth/flat shading.

After a remesh, the panel in the viewport's bottom left corner has every setting, to adjust and run again.

**Voxel Remesh** sits beside it: DynaMesh-style remeshing by resolution, the same density at any scale.

### Brushes

| Brush | Effect |
|---|---|
| Clay V1 | Piles up clay in strips. Good for blocking in forms |
| Clay V2 | Lays down clay more gradually. Suited for adjusting masses |
| Clay Rake V2 | Roughing in and unify surfaces with a raked texture |
| Clay Rake V3 | A softer rake with more clay build up (I don't use this much) |
| Clay Soft | Can be used for polishing forms and softening transitions (I barely use this) |
| Clay Strips Copy | Lays down heavy strips of clay with harder edges. Doesn't accumulate as quickly as clay V1 |
| Move | Can be used to extrude masses and make big adjustments to masses |

<img width="302" height="400" alt="Screenshot 2026-10-22 at 6 25 50 PM" src="https://github.com/user-attachments/assets/d6dbc2e3-d0a4-41f2-b58b-f07d627f7c82" />  

Sculpt made using these brushes

### Keys (only take effect in Sculpt Mode)

| Action | Keys |
|---|---|
| Rotate / move / zoom | Right drag / Alt+right drag / Ctrl+right-drag, or drag on empty canvas |
| Invert the brush | Alt+drag |
| Mask / erase mask | Ctrl+drag / Ctrl+Alt+drag |
| Invert mask / box mask | Ctrl+click / Ctrl+drag on empty canvas |
| Size / strength | S / U |
| Frame / wireframe / perspective | F / Shift+F / P |
| Symmetry / brush picker | X / B |
| Subdivision up / down, Divide | D / Shift+D, Ctrl+D |
| Sculpt menu | Space |

Keybinds can be turned on or off in Preferences. Right-drag navigation can optionally work in every mode.

## Building from source
I've only tested in Blender 5.2.2
```bash
tools/build.sh      # build the zip into dist/
tests/run.sh        # run the tests in a throwaway profile (--headless to skip the interactive ones)
tools/install.sh    # install the newest zip (Blender must be closed)
```

## License

GPL-3.0-or-later.
