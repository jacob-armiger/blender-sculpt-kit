"""Build the brush libraries bundled with Sculpt Kit.

    blender -b --factory-startup --python tools/build_assets.py

Writes sculpt_kit/assets/kit (the kit's own brush: Move) and
sculpt_kit/assets/kingslien (the Kingslien pack, built from the ZBrush files
in brush_sources/kingslien). Overrides from tuning/brushes.toml are applied to
both, which is how pen-testing feedback gets baked in.
"""
import glob
import os
import sys
import tomllib

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "sculpt_kit")
sys.path[:0] = [PACKAGE, os.path.join(ROOT, "tools")]
import brush_settings  # noqa: E402
import kingslien  # noqa: E402

ASSETS = os.path.join(PACKAGE, "assets")
KIT_ALPHAS = os.path.join(ROOT, "build", "kit_alphas")  # intermediate; the images are packed into the .blend
KIT_CATALOG_ID = "3c0d1a52-7e41-4b4e-8f6a-2d9b61c7a0e4"
KIT_CATALOG = "Sculpt Kit"

def load_tuning():
    with open(os.path.join(ROOT, "tuning", "brushes.toml"), "rb") as f:
        return tomllib.load(f).get("brushes", {})


def tune(brush, tuning):
    settings = tuning.get(brush.name)
    if not settings:
        return
    rejected = brush_settings.apply(brush, settings)
    print("  tuned %s: %d settings applied%s" % (
        brush.name, len(settings) - len(rejected), ", could not apply: " + ", ".join(rejected) if rejected else ""))


def set_preview(idblock, grey):
    """Use a 256 x 256 greyscale image (top row first) as the asset thumbnail."""
    rgba = np.empty((256, 256, 4), dtype=np.float32)
    rgba[..., :3] = grey[::-1, :, None]
    rgba[..., 3] = 1.0
    idblock.preview_ensure()
    idblock.preview.image_size = (256, 256)
    idblock.preview.image_pixels_float.foreach_set(rgba.ravel())


def arrow_image():
    """A plain arrow, so the Move brush is easy to pick out in the brush shelf."""
    y, x = np.mgrid[0:256, 0:256]
    shaft = (np.abs(y - 128) <= 10) & (x >= 48) & (x <= 160)
    head = (x >= 150) & (x <= 214) & (np.abs(y - 128) <= (214 - x) * 0.75)
    return np.where(shaft | head, 1.0, 0.0)


def as_asset(brush, description):
    brush.asset_clear()
    brush.asset_mark()
    meta = brush.asset_data
    meta.catalog_id = KIT_CATALOG_ID
    meta.author = "Sculpt Kit"
    meta.description = description
    meta.tags.new("Sculpt Kit")


def build_kit(tuning):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # some brush options (a texture that follows the stroke) only exist while in Sculpt Mode
    mesh = bpy.data.meshes.new("tmp")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    ob = bpy.data.objects.new("tmp", mesh)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='SCULPT')

    essentials = glob.glob(os.path.join(bpy.utils.system_resource('DATAFILES'),
                                        "assets", "brushes", "*mesh_sculpt*.blend"))[0]
    with bpy.data.libraries.load(essentials) as (_src, dst):
        dst.brushes = ["Grab"]
    grab = dst.brushes[0]

    # Move: Blender's Grab only reaches the surface nearest the click. Projected
    # falloff takes everything under the brush circle, front and back, the way
    # ZBrush's Move brush does.
    move = grab.copy()
    move.name = "SculptKit_Move"
    move.falloff_shape = 'PROJECTED'
    move.strength = 0.8
    as_asset(move, "Moves everything under the brush, front and back, like ZBrush's Move")
    set_preview(move, arrow_image())
    tune(move, tuning)

    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.objects.remove(ob)
    bpy.data.meshes.remove(mesh)
    # entering Sculpt Mode links Blender's own default brush; keep only the kit's
    for brush in [b for b in bpy.data.brushes if not b.name.startswith("SculptKit_")]:
        bpy.data.brushes.remove(brush)
    for library in list(bpy.data.libraries):
        bpy.data.libraries.remove(library)

    folder = os.path.join(ASSETS, "kit")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "blender_assets.cats.txt"), "w") as f:
        f.write("# Asset catalogs for Sculpt Kit's own brushes.\n\nVERSION 1\n\n"
                "%s:%s:%s\n" % (KIT_CATALOG_ID, KIT_CATALOG, KIT_CATALOG))
    path = os.path.join(folder, "SculptKit_Brushes.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    print("Saved", path, [b.name for b in bpy.data.brushes])


def main():
    bpy.context.preferences.filepaths.save_version = 0  # no .blend1 backups next to the libraries
    tuning = load_tuning()
    build_kit(tuning)
    kingslien.build(lambda brush: tune(brush, tuning))


if __name__ == "__main__":
    main()
