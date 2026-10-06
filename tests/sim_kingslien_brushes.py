"""Interactive test: every Kingslien brush leaves the right kind of mark, and pen pressure never shrinks them to nothing.

Also saves tests/out/kingslien_strokes.png, one stroke per brush from top to
bottom, for a look at how they read.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Euler, Vector  # noqa: E402

import simlib  # noqa: E402

LIBRARY = "Sculpt Kit Kingslien"
# brush -> what its stroke should do to a flat surface
BRUSHES = (
    ("Kingslien_Clay", "raise"),
    ("Kingslien_Rake", "raise"),
    ("Kingslien_Rake_V2", "raise"),
    ("Kingslien_HairTexture", "both"),
    ("Kingslien_Knife", "cut"),
    ("Kingslien_KnifeSoft_DS", "cut"),
)
SIZE = 200  # brush diameter in pixels


def activate(sim, name):
    with sim.override():
        bpy.ops.brush.asset_activate(asset_library_type='CUSTOM', asset_library_identifier=LIBRARY,
                                     relative_asset_identifier="Kingslien_Brushes.blend/Brush/" + name)


def test(sim):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    with sim.override():
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=1500, y_subdivisions=1000, size=1)
        plane = bpy.context.active_object
        plane.scale = (3, 2, 1)
        bpy.ops.object.transform_apply(scale=True)
    space, rv3d = sim.space(), sim.rv3d()
    space.overlay.show_overlays = False
    space.shading.type = 'SOLID'
    space.shading.light = 'MATCAP'
    space.shading.show_cavity = True
    space.shading.cavity_type = 'BOTH'
    space.shading.color_type = 'SINGLE'
    space.shading.single_color = (0.8, 0.8, 0.8)
    space.show_region_ui = space.show_region_toolbar = space.show_gizmo = False
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_location = (0, 0, 0)
    rv3d.view_distance = 2.25
    rv3d.view_rotation = Euler((0, 0, 0)).to_quaternion()
    with sim.override():
        bpy.ops.object.mode_set(mode='SCULPT')
    sculpt = bpy.context.tool_settings.sculpt
    sculpt.use_symmetry_x = False
    sculpt.unified_paint_settings.use_unified_size = True
    sculpt.unified_paint_settings.use_unified_strength = False
    yield 0.8

    def heights():
        return sim.positions(plane)[:, 2].copy()

    # ---------------------------------------------- one mouse stroke per brush
    rows = len(BRUSHES)
    previous = heights()
    for row, (name, expected) in enumerate(BRUSHES):
        activate(sim, name)
        yield 0.3
        brush = sculpt.brush
        sim.check("%s is in the brush library" % name, brush is not None and brush.name == name, brush and brush.name)
        sculpt.unified_paint_settings.size = SIZE
        y = 0.83 - 1.66 * (row + 0.5) / rows

        def point(t):
            return sim.project((-1.3 + 2.6 * t, y + math.sin(t * math.tau) * 0.07, 0.0))

        steps = 70
        sim._send('MOUSEMOVE', 'NOTHING', point(0), {})
        yield 0.05
        sim._send('LEFTMOUSE', 'PRESS', point(0), {})
        yield 0.05
        for k in range(1, steps + 1):
            sim._send('MOUSEMOVE', 'NOTHING', point(k / steps), {})
            yield 0.012
        sim._send('LEFTMOUSE', 'RELEASE', point(1), {})
        yield 0.4
        now = heights()
        delta = now - previous
        previous = now
        up, down = float(delta.max()), float(-delta.min())
        ok = {"raise": up > 0.004 and down < 0.2 * up,
              "cut": down > 0.004 and up < 0.2 * down,
              "both": up > 0.002 and down > 0.002}[expected]
        sim.check("%s strokes %s" % (name, {"raise": "build up", "cut": "cut in", "both": "raise and cut"}[expected]),
                  ok, "up %.4f, down %.4f" % (up, down))

    sim._send('MOUSEMOVE', 'NOTHING', sim.at(0.01, 0.01), {})
    sim.area.tag_redraw()
    yield 0.8
    with sim.override():
        bpy.ops.screen.screenshot_area(filepath=os.path.join(simlib.OUT, "kingslien_strokes.png"))

    # ------------------------------------------------------- pressure curves
    # Simulated mouse events carry no pen pressure, so these strokes are
    # scripted with a pressure value instead. Measure how wide a stroke is at a
    # very light touch compared with full pressure.
    element = {p.identifier for p in bpy.types.OperatorStrokeElement.bl_rna.properties}

    def scripted_width(x_centre, pressure):
        """Draw a short vertical stroke at this pressure and return how wide a mark it left."""
        before = heights()
        region = sim.region()
        points = []
        for k in range(40):
            t = k / 39
            location = Vector((x_centre, -0.95 + 0.25 * t, 0.0))
            mouse = sim.project(location)
            item = {"name": "", "location": location, "mouse": (mouse[0] - region.x, mouse[1] - region.y),
                    "mouse_event": mouse, "pressure": pressure, "size": SIZE, "time": t, "is_start": k == 0,
                    "x_tilt": 0.0, "y_tilt": 0.0, "pen_flip": False}
            points.append({key: value for key, value in item.items() if key in element})
        with sim.override():
            bpy.ops.sculpt.brush_stroke(stroke=points)
        co = sim.positions(plane)
        delta = np.abs(co[:, 2] - before)
        band = np.abs(co[:, 1] + 0.83) < 0.002  # a line across the middle of the stroke
        touched = band & (delta > 0.25 * delta[band].max()) if delta[band].max() > 0 else band & False
        return float(np.ptp(co[touched, 0])) if touched.any() else 0.0

    # Clay-type brushes are no use for this check: Blender gives Clay Strips a
    # floor of its own, whatever the curve says. The crease-type knife has none.
    activate(sim, "Kingslien_KnifeSoft_DS")
    yield 0.3
    sculpt.unified_paint_settings.size = SIZE
    full = scripted_width(-1.2, 1.0)
    yield 0.2
    light = scripted_width(-0.9, 0.05)
    yield 0.2
    ratio = light / full if full else 0.0
    sim.check("Kingslien_KnifeSoft_DS at a light touch keeps about a quarter of its width", 0.18 < ratio < 0.45,
              "light %.3f / full %.3f = %.2f" % (light, full, ratio))

    with sim.override():
        bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS',
                                     relative_asset_identifier="brushes/essentials_brushes-mesh_sculpt.blend/Brush/Crease Sharp")
    yield 0.3
    sculpt.brush.use_pressure_size = True
    sculpt.unified_paint_settings.size = SIZE
    full = scripted_width(-0.6, 1.0)
    yield 0.2
    light = scripted_width(-0.3, 0.05)
    yield 0.2
    ratio = light / full if full else 0.0
    sim.check("Blender's own Crease Sharp nearly vanishes at the same touch (for comparison)", ratio < 0.15,
              "light %.3f / full %.3f = %.2f" % (light, full, ratio))


simlib.run("sim_kingslien_brushes", test)
