"""Build the brush libraries bundled with Sculpt Kit.

    blender -b --factory-startup --python tools/build_assets.py

Writes sculpt_kit/assets/kit (the kit's own brushes: Move, the five-dot rake, the clay rake, a copy of
Kingslien_Clay and plain copies of Blender's Clay Strips and Clay) and
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


def raked_strip(centres=(-0.3625, 0.3625), groove=0.375, depth=0.4, wall=0.015, taper=0.17, size=512):
    """The clay rake's alpha: a flat bar the width of the brush with evenly spaced grooves along it.

    On Clay Strips this lays a flat strip like Clay Strips' own, with shallow, wide grooves through it, so
    strokes laid side by side build an even surface with the texture on it. What Jacob asked for on
    21 October, after a round pad on Blender's Clay brush built mounds that read as lumpy over several
    strokes: flat strips, wide even grooves, nothing random. Measured at full pressure, in brush radii:
    the strip stands 0.18 high and each groove dips about a third of that.

    The grooves, centred at `centres`, are square-cut, `groove` wide with walls `wall` wide, and the strip's
    own sides are steep too, so each ridge ends in a hard, square edge (asked for the same day).

    With `taper`, each spine narrows to a point towards the front and back of the pad (y is along the stroke
    when the texture follows it), so the spines taper off where a stroke starts and ends: past half way out,
    every groove and the bar's sides close in by up to `taper` on each side of a spine."""
    x = np.linspace(-1.0, 1.0, size)[None, :]
    y = np.linspace(-1.0, 1.0, size)[:, None]
    def fade(v, start, end):
        t = np.clip((end - np.abs(v)) / (end - start), 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

    closing = taper * np.clip((np.abs(y) - 0.5) / 0.48, 0.0, 1.0)
    cut = np.zeros((size, size))
    for centre in centres:
        cut = np.maximum(cut, fade(x - centre, groove / 2 - wall + closing, groove / 2 + wall + closing))
    return fade(x, 0.9 - closing, 0.97 - closing) * fade(y, 0.75, 0.98) * (1.0 - depth * cut)


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
        dst.brushes = ["Grab", "Clay", "Clay Strips"]
    grab, clay, clay_strips = dst.brushes
    clay_rake = clay_strips.copy()

    # Move: Blender's Grab only reaches the surface nearest the click. Projected
    # falloff takes everything under the brush circle, front and back, the way
    # ZBrush's Move brush does.
    move = grab.copy()
    move.name = "SculptKit_Move"
    move.falloff_shape = 'PROJECTED'
    move.strength = 0.8
    as_asset(move, "Moves everything under the brush, front and back. "
                   "Good for pulling out limbs and other forms, and for big changes of shape")
    set_preview(move, arrow_image())
    tune(move, tuning)

    # Five-dot rake: a rake someone shared on Discord, built on Blender's Clay brush (not Clay Strips) with
    # an alpha of five soft dots in a row. Every setting below is theirs, read off their screenshots of the
    # brush in Blender 5.2; their image is used as it is, so that it is read the way theirs was.
    rake = clay.copy()
    rake.name = "SculptKit_Rake_Dots"
    as_asset(rake, "Clay rake with five round teeth that follow the stroke. Settings and alpha shared on Discord")
    rake.size = 100
    rake.use_locked_size = 'VIEW'
    rake.strength = 0.5
    rake.use_pressure_strength = True
    rake.use_pressure_size = False
    rake.direction = 'ADD'
    rake.normal_radius_factor = 0.75
    rake.hardness = 0.65
    rake.auto_smooth_factor = 0.25
    rake.use_inverse_smooth_pressure = True
    rake.plane_offset = 0.4
    rake.use_plane_trim = False
    rake.use_accumulate = True
    rake.use_frontface = False
    rake.sculpt_plane = 'AREA'
    rake.use_original_normal = False
    rake.use_original_plane = False
    rake.stroke_method = 'SPACE'
    rake.spacing = 3
    rake.use_scene_spacing = 'VIEW'
    rake.use_space_attenuation = True
    rake.jitter = 0.0
    rake.input_samples = 1
    rake.use_smooth_stroke = False
    dots = bpy.data.images.load(os.path.join(ROOT, "brush_sources", "shared", "rake_five_dots.png"))
    dots.name = "SculptKit_Rake_Dots_alpha"
    dots.pack()
    texture = bpy.data.textures.new("SculptKit_Rake_Dots_alpha", 'IMAGE')
    texture.image = dots
    kingslien.use_alpha_texture(rake, texture)
    grey = np.array(dots.pixels[:]).reshape(dots.size[1], dots.size[0], -1)[::-2, ::2, 0]
    set_preview(rake, grey)
    tune(rake, tuning)

    # Clay rake: Jacob asked on 21 October for a rake with the raked texture of a ZBrush figure study, meaty
    # enough to build small and medium forms, laying flat strips like Clay Strips with grooves through them.
    # Pen pressure scales size and strength from 60%, so a normal touch still builds. Two earlier versions
    # (four square spines; three tapered spines) were dropped the same day, which is why this one is V3:
    # three thin spines with square sides and wide, shallow grooves. The strip keeps the brush's width.
    # The spines' ends taper to points, so they thin out where a stroke starts and ends (asked for 22 October).
    pad = raked_strip()
    clay_rake.name = "SculptKit_Clay_Rake_V3"
    as_asset(clay_rake, "Lays flat strips of clay like Clay Strips, with three thin, square-cut spines and wide, shallow grooves")
    clay_rake.tip_roundness = 0.4
    clay_rake.strength = 0.6
    clay_rake.use_accumulate = True
    clay_rake.auto_smooth_factor = 0.0  # smoothing as it goes would wash the lines out
    clay_rake.spacing = 3  # dabs close enough that a small stroke reads as ridges, not a row of beads
    clay_rake.use_pressure_size = True
    clay_rake.use_pressure_strength = True
    brush_settings.set_curve(clay_rake.curve_size, [(0.0, 0.6), (1.0, 1.0)])
    brush_settings.set_curve(clay_rake.curve_strength, [(0.0, 0.6), (1.0, 1.0)])
    kingslien.use_alpha_texture(clay_rake, kingslien.make_texture("SculptKit_Clay_Rake_V3_alpha", pad, KIT_ALPHAS))
    set_preview(clay_rake, pad[::2, ::2])
    tune(clay_rake, tuning)

    # Plain copies of Blender's Clay Strips and Clay, so they sit with the kit's brushes and can be retuned
    # without touching Blender's own. Clay is named Clay Soft at Jacob's request.
    for base, name, description in (
            (clay_strips, "SculptKit_Clay_Strips_Copy",
             "Lays flat, square-edged strips of clay that build up in layers. Good for blocking in planes "
             "and muscle masses.\nA copy of Blender's Clay Strips"),
            (clay, "SculptKit_Clay_Soft",
             "Builds soft, rounded volume quickly. Good for bulking out masses and filling in between forms.\n"
             "A copy of Blender's Clay brush")):
        brush = base.copy()
        brush.name = name
        as_asset(brush, description)
        tune(brush, tuning)

    # A copy of Kingslien_Clay as Jacob tuned it, under the kit's name. The Kingslien library is built first,
    # so this is taken from it with that tuning already applied.
    with bpy.data.libraries.load(os.path.join(ASSETS, "kingslien", "Kingslien_Brushes.blend")) as (_src, dst):
        dst.brushes = ["Kingslien_Clay"]
    sk_clay = dst.brushes[0]
    sk_clay.name = "SculptKit_Clay"
    as_asset(sk_clay, "Round clay brush that lays flat-topped strokes and covers what is under them. Good for "
                      "building up and blocking in forms")
    kingslien.set_preview(sk_clay, kingslien.alpha_array(kingslien.load_zbp("Kingslien_Clay")))
    tune(sk_clay, tuning)

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
    kingslien.build(lambda brush: tune(brush, tuning))
    build_kit(tuning)  # second: it copies Kingslien_Clay out of the Kingslien library


if __name__ == "__main__":
    main()
