"""Build the brush library bundled with Sculpt Kit.

    blender -b --factory-startup --python tools/build_assets.py

Writes sculpt_kit/assets/kit/SculptKit_Brushes.blend, the brushes the add-on ships: clay, the clay rake,
the thin-toothed and five-dot rakes, Move, and plain copies of Blender's Clay Strips and Clay. Overrides
from tuning/brushes.toml are applied, which is how pen-testing feedback gets baked in.

brush_sources/, which is not in the repository, holds the five-dot rake's alpha and the ZBrush files the
Kingslien library is built from, for local use only (see kingslien.py). Without those files the brushes that
need them are left out, with a note; the library in the repository has them.

Archived brushes (earlier versions and the original conversions, kept to try out) go to a library of their
own in brush_sources/archive_library, for local use only: added to Blender as an asset library by hand, it
shows as an Archive tab. A release build (tools/build.sh --release, which sets SCULPTKIT_RELEASE=1) skips it.
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
# Sub-catalogs, so that the brushes group in the brush picker and in the shelf's Sculpt Kit tab: clay first, then
# the rakes, then Move (Jacob's order, 22 October). The shelf lists a catalog's own brushes before its
# sub-catalogs', which come in name order, and sorts by name within each.
SHAPE_CATALOG_ID = "78ee2544-4ef6-4fb0-b510-a7df39259d8e"
CLAY_CATALOG_ID = "135916c4-880f-4bfa-b2c0-7b9e7ca800d4"
RAKES_CATALOG_ID = "2fb43b74-abfa-4da4-a8a7-397a4c22aae0"
GROUPS = {
    "SculptKit_Clay_Soft": CLAY_CATALOG_ID, "SculptKit_Clay_Strips_Copy": CLAY_CATALOG_ID,
    "SculptKit_Clay_V1": CLAY_CATALOG_ID, "SculptKit_Clay_V2": CLAY_CATALOG_ID,
    "SculptKit_Clay_Rake_V2": RAKES_CATALOG_ID, "SculptKit_Clay_Rake_V3": RAKES_CATALOG_ID,
    "SculptKit_Move": SHAPE_CATALOG_ID,
}
RELEASE = os.environ.get("SCULPTKIT_RELEASE") == "1"
ARCHIVE = ("SculptKit_Kingslien_Clay", "SculptKit_Kingslien_Rake", "SculptKit_Kingslien_Rake_V2",
           "SculptKit_Rake", "SculptKit_Rake_Fine")  # local only
ARCHIVE_FOLDER = os.path.join(ROOT, "brush_sources", "archive_library")
ARCHIVE_CATALOG_ID = "ac276eaf-4e68-47b2-9ef8-7c3ef3612b81"
ARCHIVE_CATALOG = "Archive"

# The thin-toothed rakes: name, number of teeth, how much of each tooth's share of the width is ridge, description.
# Built in 0.3, dropped in 0.4.9, and brought back on 22 October at Jacob's request.
RAKES = (
    ("SculptKit_Rake", 5, 0.55,
     "Clay rake with five thin teeth that follow the stroke. Good for a scratched finish on a medium mesh"),
    ("SculptKit_Rake_Fine", 10, 0.5,
     "Fine clay rake with ten teeth. Good for a wire-rake finish; needs a dense mesh to keep its lines"),
)
RAKE_FALLOFF = [(0.0, 1.0), (0.45, 1.0), (0.75, 0.45), (1.0, 0.0)]  # the outer teeth bite less than the middle ones
RAKE_PRESSURE = [(0.0, 0.2), (1.0, 1.0)]  # share of the strength at the lightest touch and at full pressure

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


def disc_image(size=256):
    """A round, flat-topped disc with a soft rim: the Clay brush's thumbnail."""
    y, x = np.mgrid[0:size, 0:size]
    r = np.hypot(x - (size - 1) / 2, y - (size - 1) / 2) / (size / 2)
    t = np.clip((0.86 - r) / 0.2, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def thin_rake_alpha(teeth, ridge, size=512, margin=0.07, uneven=0.6, seed=7):
    """A row of teeth across the stroke, the same all the way along it.

    No real rake is perfect, so each tooth is a little off in height, width and
    position. That keeps the marks from looking ruled. The same seed gives the
    same teeth on every build.
    """
    rng = np.random.default_rng(seed)
    across = (np.arange(size) + 0.5) / size
    profile = np.zeros(size)
    pitch = (1.0 - 2.0 * margin) / teeth
    for k in range(teeth):
        centre = margin + (k + 0.5) * pitch + rng.uniform(-1.0, 1.0) * uneven * pitch * 0.25
        height = 1.0 - rng.uniform(0.0, 1.0) * uneven * 0.5
        half_width = ridge * pitch * 0.5 * (1.0 + rng.uniform(-1.0, 1.0) * uneven * 0.3)
        profile = np.maximum(profile, height * np.clip(1.0 - np.abs(across - centre) / half_width, 0.0, 1.0) ** 0.7)
    return np.tile(profile, (size, 1))


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

    # Clay rake: Jacob asked on 21 October for a rake with the raked texture of a ZBrush figure study, meaty
    # enough to build small and medium forms, laying flat strips like Clay Strips with grooves through them.
    # Pen pressure scales size and strength from 60%, so a normal touch still builds. Two earlier versions
    # (four square spines; three tapered spines) were dropped the same day, which is why this one is V3:
    # three thin spines with square sides and wide, shallow grooves. The strip keeps the brush's width.
    # The spines' ends taper to points, so they thin out where a stroke starts and ends (asked for 22 October).
    pad = raked_strip()
    clay_rake.name = "SculptKit_Clay_Rake_V3"
    as_asset(clay_rake, "Lays flat strips of clay with three thin, square-cut spines. "
                        "Good for carving forms and creating a unifying texture")
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
             "and large forms.\nA copy of Blender's Clay Strips"),
            (clay, "SculptKit_Clay_Soft",
             "Builds soft, rounded volume. Good for polishing forms and softening the transitions between "
             "them.\nA copy of Blender's Clay brush")):
        brush = base.copy()
        brush.name = name
        as_asset(brush, description)
        tune(brush, tuning)

    for name, teeth, ridge, description in () if RELEASE else RAKES:
        alpha = thin_rake_alpha(teeth, ridge)
        rake = clay_strips.copy()
        rake.name = name
        as_asset(rake, description)
        rake.tip_roundness = 0.0
        rake.use_accumulate = False  # one pass leaves one depth of mark, however long the stroke lingers
        rake.strength = 0.45
        rake.spacing = 1  # at Blender's usual 5 each dab shows, and a ridge reads as a zip rather than a line
        rake.input_samples = 3
        # A rake's teeth stay the same distance apart however hard it is pressed, so the pen sets
        # only how deep they bite. With size on pressure the lines wander and break up.
        rake.use_pressure_size = False
        rake.use_pressure_strength = True
        brush_settings.set_curve(rake.curve_strength, RAKE_PRESSURE)
        brush_settings.set_falloff(rake, RAKE_FALLOFF)
        kingslien.use_alpha_texture(rake, kingslien.make_texture(name + "_alpha", alpha, KIT_ALPHAS))
        set_preview(rake, alpha[::2, ::2])
        tune(rake, tuning)

    # Clay V2: Clay Strips with a flat-topped stroke. It began as the ZBrush port of Ryan Kingslien's Clay (V1,
    # below), but what made it so is all in its block in tuning/brushes.toml (Jacob's pen tuning, falloff and
    # pressure curves included), so it is built here on its own, with no ZBrush files needed.
    sk_clay = clay_strips.copy()
    sk_clay.name = "SculptKit_Clay_V2"
    as_asset(sk_clay, "Round clay brush that lays flat-topped strokes and covers what is under them. "
                      "Good for adjusting forms")
    set_preview(sk_clay, disc_image())
    tune(sk_clay, tuning)

    # Kingslien's Clay and rakes as the port first converts them, before any pen tuning. They go to the Archive
    # library as they are; Clay and Rake V2 are also copied into the kit as Clay V1 and Rake V2 (Jacob's pick,
    # 22 October), so these need the ZBrush files in brush_sources even for a release.
    if kingslien.available():
        kingslien.build_brushes()
        original = bpy.data.brushes["Kingslien_Clay"]
        original.name = "SculptKit_Kingslien_Clay"
        as_asset(original, "Kingslien's Clay as converted from ZBrush, before any pen tuning. "
                           "Good for comparing against Clay")
        kingslien.set_preview(original, kingslien.alpha_array(kingslien.load_zbp("Kingslien_Clay")))
        # Archive: both of Kingslien's rakes as first converted (0.3.1): ZBrush's alpha as it is, before the
        # notches and crown it was later reshaped with, at strength 0.5, with no pen tuning.
        for stem in ("Kingslien_Rake", "Kingslien_Rake_V2"):
            z = kingslien.load_zbp(stem)
            tines = kingslien.adjusted_alpha(z, 512)
            rake = clay_strips.copy()
            rake.name = "SculptKit_" + stem
            as_asset(rake, "%s as first converted from ZBrush: three tines, before any reshaping or pen tuning. "
                           "Good for comparing against the kit's rakes" % stem.replace("_", " "))
            rake.tip_roundness = 0.0
            rake.use_accumulate = bool(z['params']['Buildup'][0][1])
            rake.strength = 0.5
            rake.spacing = 5
            kingslien.pressure_curves(rake, z)
            kingslien.set_falloff(rake, RAKE_FALLOFF)
            kingslien.use_alpha_texture(rake, kingslien.make_texture(rake.name + "_alpha", tines, KIT_ALPHAS))
            if z['params']['Depth Mask'][0][1]:  # V2 limits how far from the surface it works (ZBrush Depth Mask)
                rake.use_plane_trim = True
                rake.plane_trim = kingslien.param(z, 'OuterDepth') - kingslien.param(z, 'InnerDepth')
            kingslien.set_preview(rake, tines)

        for source, name, description in (
                ("SculptKit_Kingslien_Clay", "SculptKit_Clay_V1",
                 "Round clay brush that builds up firmly and buries what is under it. Good for blocking in forms"),
                ("SculptKit_Kingslien_Rake_V2", "SculptKit_Clay_Rake_V2",
                 "Clay rake with three tines that follow the stroke. Good for roughing in forms with a raked texture")):
            copy = bpy.data.brushes[source].copy()
            copy.name = name
            as_asset(copy, description)
            if source == "SculptKit_Kingslien_Clay":
                kingslien.set_preview(copy, kingslien.alpha_array(kingslien.load_zbp("Kingslien_Clay")))
            else:
                kingslien.set_preview(copy, kingslien.adjusted_alpha(kingslien.load_zbp("Kingslien_Rake_V2"), 512))
            tune(copy, tuning)
    else:
        print("  left out SculptKit_Clay_V1 and SculptKit_Clay_Rake_V2: no ZBrush files in", kingslien.SRC)

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
        f.write("# Asset catalogs for Sculpt Kit's own brushes.\n\nVERSION 1\n\n")
        for catalog_id, path in ((KIT_CATALOG_ID, KIT_CATALOG), (CLAY_CATALOG_ID, KIT_CATALOG + "/Clay"),
                                 (RAKES_CATALOG_ID, KIT_CATALOG + "/Rakes"), (SHAPE_CATALOG_ID, KIT_CATALOG + "/Shape")):
            f.write("%s:%s:%s\n" % (catalog_id, path, path.replace("/", "-")))
    archive = {b for b in bpy.data.brushes if b.name in ARCHIVE}
    if RELEASE:  # a release leaves the local Archive library as it is: it skips some of what goes there
        for brush in archive:
            bpy.data.brushes.remove(brush)
        archive = set()
    if archive:
        os.makedirs(ARCHIVE_FOLDER, exist_ok=True)
        with open(os.path.join(ARCHIVE_FOLDER, "blender_assets.cats.txt"), "w") as f:
            f.write("# Asset catalogs for Sculpt Kit's archived brushes.\n\nVERSION 1\n\n"
                    "%s:%s:%s\n" % (ARCHIVE_CATALOG_ID, ARCHIVE_CATALOG, ARCHIVE_CATALOG))
        for brush in archive:
            brush.asset_data.catalog_id = ARCHIVE_CATALOG_ID
        archive_path = os.path.join(ARCHIVE_FOLDER, "Archive_Brushes.blend")
        bpy.data.libraries.write(archive_path, archive, compress=True)
        print("Saved", archive_path, sorted(b.name for b in archive))
        for brush in archive:
            bpy.data.brushes.remove(brush)
    stale = os.path.join(folder, "SculptKit_Trial.blend")  # where 0.4.40's trial brushes went
    if os.path.exists(stale):
        os.remove(stale)
    for brush in bpy.data.brushes:
        if brush.name in GROUPS:
            brush.asset_data.catalog_id = GROUPS[brush.name]
    path = os.path.join(folder, "SculptKit_Brushes.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    print("Saved", path, [b.name for b in bpy.data.brushes])


def main():
    bpy.context.preferences.filepaths.save_version = 0  # no .blend1 backups next to the libraries
    tuning = load_tuning()
    if kingslien.available():
        kingslien.build(lambda brush: tune(brush, tuning))  # the local Kingslien library
    build_kit(tuning)


if __name__ == "__main__":
    main()
