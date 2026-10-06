"""Interactive test: each of the kit's rakes leaves one thin, separate ridge per tooth.

Also saves tests/out/rake_strokes.png: Kingslien's three-tine rake, then the
kit's Rake and Fine Rake, one stroke each from top to bottom.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Euler  # noqa: E402

import simlib  # noqa: E402

# library, file, brush, teeth (None: only drawn for the picture)
BRUSHES = (
    ("Sculpt Kit Kingslien", "Kingslien_Brushes.blend", "Kingslien_Rake", None),
    ("Sculpt Kit", "SculptKit_Brushes.blend", "SculptKit_Rake", 5),
    ("Sculpt Kit", "SculptKit_Brushes.blend", "SculptKit_Rake_Fine", 10),
)
SIZE = 220  # brush diameter in pixels


def ridges(height):
    """The peaks of a height profile taken across a stroke, and the lowest point between each pair."""
    height = np.convolve(height, np.ones(3) / 3.0, mode='same')
    floor = 0.15 * height.max()
    peaks = [i for i in range(1, len(height) - 1)
             if height[i] > floor and height[i] > height[i - 1] and height[i] >= height[i + 1]]
    valleys = [float(height[a:b].min()) for a, b in zip(peaks, peaks[1:])]
    return [float(height[i]) for i in peaks], valleys


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

    flat = sim.positions(plane)
    middle = flat[np.abs(flat[:, 0]).argmin(), 0]
    column = np.abs(flat[:, 0] - middle) < 1e-5  # the line of vertices that crosses every stroke at its middle
    order = np.argsort(flat[column, 1])
    across = flat[column, 1][order]
    pixels_per_unit = abs(sim.project((1.0, 0.0, 0.0))[0] - sim.project((0.0, 0.0, 0.0))[0])
    reach = 0.5 * SIZE / pixels_per_unit

    previous = flat[:, 2].copy()
    for row, (library, blend, name, teeth) in enumerate(BRUSHES):
        with sim.override():
            bpy.ops.brush.asset_activate(asset_library_type='CUSTOM', asset_library_identifier=library,
                                         relative_asset_identifier="%s/Brush/%s" % (blend, name))
        yield 0.3
        brush = sculpt.brush
        sim.check("%s is in the brush library" % name, brush is not None and brush.name == name, brush and brush.name)
        sculpt.unified_paint_settings.size = SIZE
        y = 0.62 - 0.62 * row
        steps = 70
        start, end = sim.project((-1.25, y, 0.0)), sim.project((1.25, y, 0.0))
        sim._send('MOUSEMOVE', 'NOTHING', start, {})
        yield 0.05
        sim._send('LEFTMOUSE', 'PRESS', start, {})
        yield 0.05
        for k in range(1, steps + 1):
            t = k / steps
            sim._send('MOUSEMOVE', 'NOTHING', (int(start[0] + (end[0] - start[0]) * t), start[1]), {})
            yield 0.012
        sim._send('LEFTMOUSE', 'RELEASE', end, {})
        yield 0.4
        now = sim.positions(plane)[:, 2]
        delta = now - previous
        previous = now.copy()
        if teeth is None:
            continue
        sim.check("%s builds up without digging in" % name, delta.max() > 0.003 and -delta.min() < 0.2 * delta.max(),
                  "up %.4f, down %.4f" % (delta.max(), -delta.min()))
        profile = delta[column][order]
        peaks, valleys = ridges(profile[np.abs(across - y) < reach])
        sim.check("%s leaves one ridge for each of its %d teeth" % (name, teeth), len(peaks) == teeth,
                  "%d ridges" % len(peaks))
        sim.check("with a clear gap between each pair", len(valleys) == teeth - 1
                  and all(valley < 0.5 * min(a, b) for valley, a, b in zip(valleys, peaks, peaks[1:])),
                  "deepest gap is %.0f%% of its lower ridge" % (100 * max(
                      (valley / min(a, b) for valley, a, b in zip(valleys, peaks, peaks[1:])), default=1.0)))

    sim._send('MOUSEMOVE', 'NOTHING', sim.at(0.01, 0.01), {})
    sim.area.tag_redraw()
    yield 0.8
    with sim.override():
        bpy.ops.screen.screenshot_area(filepath=os.path.join(simlib.OUT, "rake_strokes.png"))


simlib.run("sim_rake_brushes", test)
