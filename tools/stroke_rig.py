"""Stroke rig: draws one straight scripted stroke per row on a flat sheet and prints what each leaves, measured
across its middle in brush radii. For tuning brushes against references.

Run by tools/stroke_rig.sh, which sets:
  RIG_PLAN      JSON list of [brush, library, pressure, size factor, settings]. library is "kit", "kingslien",
                "blender" (Blender's own brushes) or "variants" (a library of trial brushes at RIG_VARIANTS).
                settings is a dict in tuning/brushes.toml's form, applied before the stroke.
  RIG_OUT       where to write the results and a picture
Scripted strokes get one dab per point, so the points are spaced as Blender spaces a real stroke's dabs, and
"mouse_event" is given from the canvas's corner like "mouse": from the window's, strokes high in the view
never start.
"""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Euler, Vector  # noqa: E402

import simlib  # noqa: E402

PLAN = json.loads(os.environ["RIG_PLAN"])
VARIANTS = os.environ.get("RIG_VARIANTS", "")
RADIUS = 0.07
PER_RADIUS = float(os.environ.get("RIG_PER_RADIUS", "25"))
LIBRARIES = {"variants": ("CUSTOM", "Variants", "Variants.blend/Brush/"),
             "kingslien": ("CUSTOM", "Sculpt Kit Kingslien", "Kingslien_Brushes.blend/Brush/"),
             "kit": ("CUSTOM", "Sculpt Kit", "SculptKit_Brushes.blend/Brush/"),
             "blender": ("ESSENTIALS", "", "brushes/essentials_brushes-mesh_sculpt.blend/Brush/")}


def activate(sim, name, library):
    kind, identifier, prefix = LIBRARIES[library]
    with sim.override():
        if identifier:
            bpy.ops.brush.asset_activate(asset_library_type=kind, asset_library_identifier=identifier,
                                         relative_asset_identifier=prefix + name)
        else:
            bpy.ops.brush.asset_activate(asset_library_type=kind, relative_asset_identifier=prefix + name)


def test(sim):
    brush_settings = importlib.import_module(simlib.MODULE + ".brush_settings")
    if VARIANTS and "Variants" not in bpy.context.preferences.filepaths.asset_libraries:
        bpy.context.preferences.filepaths.asset_libraries.new(name="Variants", directory=VARIANTS)
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    pitch = 3.2 * RADIUS * max(p[3] for p in PLAN)
    height = max(2.0, len(PLAN) * pitch + 0.4)
    step = RADIUS / PER_RADIUS
    with sim.override():
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=round(3.0 / step), y_subdivisions=round(height / step), size=1)
        sheet = bpy.context.active_object
        sheet.scale = (3.0, height, 1)
        bpy.ops.object.transform_apply(scale=True)
    space, rv3d = sim.space(), sim.rv3d()
    space.overlay.show_overlays = False
    space.shading.type = 'SOLID'
    space.shading.light = 'MATCAP'
    space.shading.show_cavity = False
    with sim.override():
        space.show_region_ui = space.show_region_toolbar = space.show_gizmo = False
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_location = (0, 0, 0)
    rv3d.view_rotation = Euler((0, 0, 0)).to_quaternion()
    region = sim.region()
    rv3d.view_distance = max(3.0 / 0.72, height * max(1.0, region.width / region.height) / 0.72) * 1.05
    with sim.override():
        bpy.ops.object.mode_set(mode='SCULPT')
    sculpt = bpy.context.tool_settings.sculpt
    sculpt.use_symmetry_x = False
    unified = sculpt.unified_paint_settings
    unified.use_unified_size = True
    unified.use_unified_strength = False
    yield 1.0

    region = sim.region()
    unit_px = (Vector(sim.project((1, 0, 0))) - Vector(sim.project((0, 0, 0)))).length
    element = {p.identifier for p in bpy.types.OperatorStrokeElement.bl_rna.properties}
    co0 = sim.positions(sheet)
    sim.note("one brush radius = %.0f px = %.0f mesh points; profiles run from -1 to 1 radius in steps of 0.04" % (
        RADIUS * unit_px, PER_RADIUS))
    for row, (name, library, pressure, factor, settings) in enumerate(PLAN):
        activate(sim, name, library)
        yield 0.3
        brush = sculpt.brush
        if brush is None or brush.name != name:
            sim.note("MISSING %s" % name)
            continue
        rejected = brush_settings.apply(brush, settings)
        if rejected:
            sim.note("REJECTED %s" % rejected)
        size = max(4, round(2 * RADIUS * factor * unit_px))
        unified.size = size
        y = height / 2 - 0.2 - pitch * (row + 0.5)
        brush.curve_size.initialize()
        length, t, points = 2.4, 0.0, []
        while t <= 1.0:
            location = Vector((-length / 2 + length * t, y, 0.0))
            mouse = sim.project(location)
            local = (mouse[0] - region.x, mouse[1] - region.y)
            item = {"name": "", "location": location, "mouse": local, "mouse_event": local, "pressure": pressure,
                    "size": size, "time": t, "is_start": not points, "x_tilt": 0.0, "y_tilt": 0.0, "pen_flip": False}
            points.append({key: value for key, value in item.items() if key in element})
            share = brush.curve_size.evaluate(brush.curve_size.curves[0], pressure) if brush.use_pressure_size else 1.0
            t += max(0.005, brush.spacing / 100.0) * 2 * RADIUS * factor * max(share, 0.05) / length
        before = sim.positions(sheet)[:, 2].copy()
        with sim.override():
            bpy.ops.sculpt.brush_stroke(stroke=points)
        yield 0.1
        rise = (sim.positions(sheet)[:, 2] - before) / (RADIUS * factor)
        lane = np.abs(co0[:, 0]) < 0.25
        offset = (co0[lane, 1] - y) / (RADIUS * factor)
        bins = np.linspace(-1.0, 1.0, 51)
        which = np.digitize(offset, bins) - 1
        cut = np.array([rise[lane][which == i].mean() if (which == i).any() else 0.0 for i in range(50)])
        sim.note("%-24s p=%-4s x%.1f %s  top %.3f r" % (name, pressure, factor, json.dumps(settings)[:60], float(cut.max())))
        sim.note("    " + " ".join("%2d" % round(100 * v) for v in cut))
    yield from sim.nudge()
    sim._send('MOUSEMOVE', 'NOTHING', (region.x + 5, region.y + 5), {})
    yield 0.5
    with sim.override():
        bpy.ops.screen.screenshot_area(filepath=os.path.join(simlib.OUT, "rig.png"))


simlib.run("rig", test)
