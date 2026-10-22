# Sculpt Kit

A Blender extension for sculptors: tuned clay and rake brushes, ZBrush-style navigation and keys, subtools, subdivision levels, a cleaner sculpting layout, and a reference view you can measure your sculpt against. Every feature can be turned on or off.

Requires **Blender 5.2 or newer**.

## Install

1. Download `sculpt_kit-<version>.zip` from [Releases](../../releases).
2. Drag it onto the Blender window, or use **Preferences > Get Extensions > Install from Disk** (menu at the top right).
3. Make sure **Sculpt Kit** is enabled under **Preferences > Add-ons**.
4. A welcome dialog asks whether you want the color theme and the ZBrush keys. Both stay off unless you pick them.

Disabling or uninstalling the kit puts Blender's own theme, keys and brush bar back.

Settings are in **Preferences > Add-ons > Sculpt Kit**, panels in the **Sculpt Kit** tab of the viewport sidebar (N), and brushes in the **Sculpt Kit** tab of the brush shelf.

## Features

- **Reference view:** compare your sculpt side by side with a reference, at the same view and scale
- **New Sculpt:** spawn a sphere, rounded cube or thin armature cylinder ready to sculpt
- **Subtools:** switch between, hide and organize the meshes in your scene
- **Subdivide:** quick access to Multires and subdivision while you sculpt
- **Remesh:** DynaMesh-style voxel remeshing by resolution, and self-repairing quad remeshing
- **Optional Zbrush-like UI and key bindings:** a KlaySculpt or ZBrush color theme, layout, brush bar and materials

### Brushes

| Brush | Good for |
|---|---|
| Clay V1 | Blocking in forms |
| Clay V2 | Adjusting forms |
| Clay Rake V2 | Roughing in forms with a raked texture |
| Clay Rake V3 | Carving forms and creating a unifying texture |
| Clay Soft | Polishing forms and softening transitions |
| Clay Strips Copy | Blocking in planes and large forms |
| Move | Pulling out limbs and big changes of shape |
| Rake Dots | A finer texture |

### Keys (Sculpt Mode only, when turned on)

| Action | Keys |
|---|---|
| Rotate / move / zoom | Right-drag / Alt + right-drag / Ctrl + right-drag, or drag on empty canvas |
| Invert the brush | Alt + drag |
| Mask / erase mask | Ctrl + drag / Ctrl + Alt + drag |
| Invert mask / box mask | Ctrl + click / Ctrl + drag on empty canvas |
| Size / strength | S / U |
| Frame / wireframe / perspective | F / Shift + F / P |
| Symmetry / brush picker | X / B |
| Subdivision up / down, Divide | D / Shift + D, Ctrl + D |
| Sculpt menu | Space |

Turn the keys on or off, and each group of them, in the preferences.

## Compatibility

- The color theme changes Blender's preferences, including Region Overlap, which affects every editor. Your own settings come back when you turn it off.
- If another add-on also changes Sculpt Mode keys, one of the two may take priority.

## Building from source

```bash
tools/build.sh      # build the zip into dist/
tests/run.sh        # run the tests in a throwaway profile (--headless to skip the interactive ones)
tools/install.sh    # install the newest zip (Blender must be closed)
```

These use `/Applications/Blender.app`; set `BLENDER` to use another. Some brushes are built from files not in this repository (`brush_sources/`); without them the build leaves those brushes out, but the committed library in `sculpt_kit/assets/` has them all.

To tune a brush, turn on **Brush tuning (for development)** in the preferences, adjust the brush in Blender, press **Copy Brush Settings** in the Sculpt Kit tab, paste the block into `tuning/brushes.toml`, then build and install.

## License

GPL-3.0-or-later.
