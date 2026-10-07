# Sculpt Kit

A Blender extension that makes sculpting in Blender feel like ZBrush. Every feature has its own switch.

Requires **Blender 5.2 or newer**. It is a personal kit: the Kingslien brushes use alphas taken from a purchased ZBrush brush set, so do not share the zip.

## Install on another Blender or another computer

Everything the kit needs (code, brushes, alphas, colour themes, the grey material) is inside one file: the newest `dist/sculpt_kit-<version>.zip`.

1. Copy that zip to the other machine.
2. Either drag the zip onto the Blender window and confirm, or use Preferences > Get Extensions > the arrow menu at the top right > Install from Disk.
3. Make sure it is ticked under Preferences > Add-ons > Sculpt Kit.

Your switches and choices (style, material, which features are on) are stored in each Blender's own preferences, so they do not travel with the zip. A fresh install starts with everything on, in the ZBrush style.

To update, install a newer zip the same way. To remove it, untick or uninstall it in Preferences; that puts your own theme back.

## What is in it

All switches are in Preferences > Add-ons > Sculpt Kit.

| Feature | What it does |
|---|---|
| ZBrush navigation and keys | Sculpt Mode only: right-drag and empty-canvas navigation, Alt inverts, Ctrl masks, ZBrush hotkeys (S size, U strength, F frame, X symmetry, B brushes, D / Shift+D / Ctrl+D subdivision) |
| ZBrush look | Colour theme (ZBrush or KlaySculpt style), ZBrush Layout button in the top bar, ZBrush-style brush bar and view switches, Subtools list, material (red wax, grey, brown clay). Each part has its own switch. As in ZBrush, the subtool you are working on stays in view: with its eye off, it hides when you pick another |
| Move brush | Move that grabs through the whole model |
| Kingslien brushes | Clay, Rake V2, HairTexture, Knife, KnifeSoft, ported from Ryan Kingslien's ZBrush brushes. Clay and Rake V2 are tuned with the pen against strokes made in ZBrush |
| Synced reference view | Splits the viewport: reference on the left, sculpt on the right, both kept on the same view and the same frame, level with each other. Matching Edges dims whatever one half shows and the other does not, so the edge of the picture can be measured against like calipers |
| New sculpt sphere or cube | Dense quad sphere, or cube with rounded edges, at 25 thousand to 1.5 million faces, in Sculpt Mode, viewport set up. The cube's Rounding can be changed right after adding it. Every shape is the size of Blender's starting cube, 2 units across. The first goes to the centre of the scene and Blender's untouched starting cube is hidden to make room. One added to a sculpt goes on the surface in the middle of the view. The view only moves when it does not already show the sphere |
| Remesh tools | Voxel Remesh by resolution on Ctrl+R; Quad Remesh that repairs the mesh first |
| Brush tuning helper | Copy Tuning copies the active brush's settings; Reset to Saved returns the brush to the settings in its library |

The panels are in the 3D viewport sidebar (N), in the Sculpt Kit tab. The brushes are in the Sculpt Mode brush shelf.

## Working on the kit

Run these from the project folder. They use `/Applications/Blender.app`; set `BLENDER` to use another.

```bash
tools/build.sh
```

Builds the brush libraries, colour themes and grey material, and writes the zip to `dist/`. It stops if any step fails.

```bash
tests/run.sh
```

Runs every check in a throwaway Blender profile (`.test-profile`), so your own preferences are never touched. Blender windows open and close by themselves while it runs. `tests/run.sh --headless` skips those; `tests/run.sh sim_reference_view` runs one interactive test.

```bash
tools/install.sh
```

Installs the newest zip into this machine's Blender. Blender must be closed.

Bump `version` in `sculpt_kit/blender_manifest.toml` before building a new release.

## Where things are

| Path | Contents |
|---|---|
| `sculpt_kit/` | The extension itself. `features/` has one module per feature |
| `sculpt_kit/assets/` | Built brush libraries and the grey matcap (generated; do not edit by hand) |
| `tools/` | Build scripts: `build_assets.py` (kit brushes), `kingslien.py` (ZBrush brush port), `build_theme.py` (colour styles), `build_matcap.py` (grey material) |
| `brush_sources/kingslien/` | The original ZBrush `.ZBP` brush files |
| `tuning/brushes.toml` | Brush setting overrides from pen testing |
| `tests/` | Headless checks and simulated-input tests |
| `dist/` | Built zips |

## Tuning

- **A brush:** adjust it in Blender, press Copy Tuning in the Sculpt Kit tab, paste the block into `tuning/brushes.toml` in place of any earlier block for that brush, then build and install. Copy before quitting Blender: changes to a brush last only for the session. Reset to Saved throws them away.
- **Clay and Rake V2:** their blocks in `tuning/brushes.toml` are the pen tuning; the reasons for the settings underneath are in the comments in `tools/kingslien.py`.
- **Colours:** edit the `STYLES` table in `tools/build_theme.py`.
- **Grey material:** edit the numbers at the top of `tools/build_matcap.py`.
- **Rake shape:** `rake_alpha` in `tools/kingslien.py`: `RAKE_NOTCHES` is where the tines meet, `RAKE_CROWN` how domed each tine is.
- **Size of a new sphere or cube, and the cube's rounding:** the defaults of `radius` and `rounding` in `sculpt_kit/features/sculpt_session.py`.
