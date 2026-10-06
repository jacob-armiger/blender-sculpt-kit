"""Interactive test: ZBrush navigation, modifiers and hotkeys do what the preferences say."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402

import simlib  # noqa: E402


def test(sim):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    with sim.override():
        bpy.ops.sculptkit.new_sculpt(density='7')
    yield 1.0
    ob = bpy.context.active_object
    sculpt = bpy.context.tool_settings.sculpt
    sim.check("new sculpt sphere is ready", bpy.context.mode == 'SCULPT' and len(ob.data.polygons) == 6 * 4 ** 7,
              "%s, %d faces" % (bpy.context.mode, len(ob.data.polygons)))

    def model():
        return sim.project((0.0, 0.0, 0.0))

    def offset(point, dx, dy):
        return point[0] + dx, point[1] + dy

    def outward(before):
        """Net movement of the surface away from the sphere's centre since `before`."""
        moved = sim.positions() - before
        normals = before / np.linalg.norm(before, axis=1)[:, None]
        return float((moved * normals).sum())

    def reset_view():
        with sim.override():
            bpy.ops.view3d.view_all()

    # Empty canvas: left of the sphere, clear of the toolbar along the viewport's left edge.
    canvas = sim.at(0.14, 0.55)
    canvas_to = sim.at(0.20, 0.67)
    canvas_corner = sim.at(0.12, 0.25)

    # ------------------------------------------------------------ sculpting
    s = sim.snapshot()
    yield from sim.drag(model(), offset(model(), 110, 30))
    c = sim.changes(s)
    sim.check("drag on the model sculpts and leaves the view alone", c["mesh"] and not c["rotated"] and not c["moved"], c)
    sim.check("a plain stroke adds clay", outward(s["co"]) > 0, round(outward(s["co"]), 4))

    s = sim.snapshot()
    yield from sim.drag(offset(model(), 0, -160), offset(model(), 110, -130), alt=True)
    c = sim.changes(s)
    sim.check("Alt + drag on the model sculpts", c["mesh"] and not c["moved"], c)
    sim.check("Alt inverts the brush", outward(s["co"]) < 0, round(outward(s["co"]), 4))

    # ----------------------------------------------------------- navigation
    s = sim.snapshot()
    yield from sim.drag(canvas, canvas_to)
    c = sim.changes(s)
    sim.check("drag on empty canvas rotates", c["rotated"] and not c["mesh"], c)
    reset_view()
    yield 0.7

    s = sim.snapshot()
    yield from sim.drag(canvas, canvas_to, alt=True)
    c = sim.changes(s)
    sim.check("Alt + drag on empty canvas moves", c["moved"] and not c["rotated"] and not c["mesh"], c)
    reset_view()
    yield 0.7

    s = sim.snapshot()
    yield from sim.drag(model(), offset(model(), 90, 50), button='RIGHTMOUSE')
    c = sim.changes(s)
    sim.check("right-drag over the model rotates without sculpting", c["rotated"] and not c["mesh"], c)
    reset_view()
    yield 0.7

    s = sim.snapshot()
    yield from sim.drag(model(), offset(model(), 90, 50), button='RIGHTMOUSE', alt=True)
    c = sim.changes(s)
    sim.check("Alt + right-drag moves", c["moved"] and not c["rotated"] and not c["mesh"], c)
    reset_view()
    yield 0.7

    s = sim.snapshot()
    yield from sim.drag(model(), offset(model(), 0, 120), button='RIGHTMOUSE', ctrl=True)
    c = sim.changes(s)
    sim.check("Ctrl + right-drag zooms", c["zoomed"] and not c["rotated"] and not c["mesh"], c)

    s = sim.snapshot()
    yield from sim.key('F')
    yield 0.6
    sim.check("F frames the model again", sim.changes(s)["zoomed"])

    # ---------------------------------------------------------------- masks
    brush_before = sculpt.brush.name
    s = sim.snapshot()
    yield from sim.drag(offset(model(), 0, 150), offset(model(), 120, 170), ctrl=True)
    c = sim.changes(s)
    sim.check("Ctrl + drag on the model paints a mask, not clay", c["mask"] and not c["mesh"] and sim.mask().max() > 0.5,
              "max mask %.2f" % sim.mask().max())
    sim.check("the brush in use comes back after masking", sculpt.brush.name == brush_before, sculpt.brush.name)

    painted = sim.mask().sum()
    yield from sim.drag(offset(model(), 0, 150), offset(model(), 120, 170), ctrl=True, alt=True)
    sim.check("Ctrl + Alt + drag erases mask", sim.mask().sum() < painted * 0.5,
              "%.1f -> %.1f" % (painted, sim.mask().sum()))

    yield from sim.drag(offset(model(), 0, 150), offset(model(), 120, 170), ctrl=True)
    mean_before = sim.mask().mean()
    yield from sim.click(canvas, ctrl=True)
    sim.check("Ctrl + click on empty canvas inverts the mask", abs(sim.mask().mean() - (1.0 - mean_before)) < 0.01,
              "mean %.3f -> %.3f" % (mean_before, sim.mask().mean()))

    yield from sim.drag(sim.at(0.08, 0.42), sim.at(0.15, 0.58), ctrl=True)
    sim.check("Ctrl + drag a box that misses the model clears the mask", sim.mask().max() < 1e-4,
              "max mask %.3f" % sim.mask().max())

    s = sim.snapshot()
    yield from sim.drag(canvas_corner, model(), ctrl=True)
    c = sim.changes(s)
    mask = sim.mask()
    sim.check("Ctrl + drag from canvas across the model box-masks part of it",
              mask.max() > 0.99 and 0.02 < mask.mean() < 0.98 and not c["rotated"], "masked share %.2f" % mask.mean())

    yield from sim.drag(canvas_corner, model(), ctrl=True, alt=True)
    sim.check("Ctrl + Alt + drag the same box from canvas unmasks it again", sim.mask().max() < 1e-4,
              "max mask %.3f" % sim.mask().max())

    # -------------------------------------------- other tools keep their keys
    with sim.override():
        bpy.ops.wm.tool_set_by_id(name="builtin.box_mask")
    yield 0.3
    s = sim.snapshot()
    yield from sim.drag(canvas_corner, model())
    c = sim.changes(s)
    sim.check("the Box Mask tool still works from empty canvas", c["mask"] and not c["rotated"] and not c["mesh"], c)
    with sim.override():
        bpy.ops.wm.tool_set_by_id(name="builtin.brush")
        bpy.ops.paint.mask_flood_fill(mode='VALUE', value=0.0)
    yield 0.3

    # -------------------------------------------------------------- hotkeys
    space, rv3d = sim.space(), sim.rv3d()
    before = ob.data.use_mirror_x
    yield from sim.key('X')
    sim.check("X toggles symmetry", ob.data.use_mirror_x != before)
    yield from sim.key('X')

    before = rv3d.is_perspective
    yield from sim.key('P')
    yield 0.4
    sim.check("P toggles perspective", rv3d.is_perspective != before)
    yield from sim.key('P')
    yield 0.4

    before = space.overlay.show_wireframes
    yield from sim.key('F', shift=True)
    sim.check("Shift + F toggles wireframe", space.overlay.show_wireframes != before)
    yield from sim.key('F', shift=True)

    unified = sculpt.unified_paint_settings

    def size():
        return unified.size if unified.use_unified_size else sculpt.brush.size

    def strength():
        return unified.strength if unified.use_unified_strength else sculpt.brush.strength

    for key, read, label in (('S', size, "S changes the brush size"), ('U', strength, "U changes the brush strength")):
        before = read()
        centre = sim.at(0.5, 0.5)
        yield from sim.key(key, at=centre)
        sim._send('MOUSEMOVE', 'NOTHING', offset(centre, 140, 0), {})
        yield 0.1
        yield from sim.click(offset(centre, 140, 0))
        sim.check(label, abs(read() - before) > 1e-4, "%s -> %s" % (round(before, 3), round(read(), 3)))

    yield from sim.key('D', ctrl=True)
    yield 0.6
    multires = next((m for m in ob.modifiers if m.type == 'MULTIRES'), None)
    sim.check("Ctrl + D divides", multires is not None and multires.total_levels == 1,
              None if multires is None else multires.total_levels)
    if multires is not None:
        yield from sim.key('D', shift=True)
        low = multires.sculpt_levels
        yield from sim.key('D')
        sim.check("Shift + D and D step the subdivision level down and up",
                  low == 0 and multires.sculpt_levels == 1, "%d then %d" % (low, multires.sculpt_levels))

    # ------------------------------------------------- switching the keys off
    sim.prefs.use_zbrush_keys = False
    yield 0.3
    s = sim.snapshot()
    yield from sim.drag(canvas, canvas_to)
    sim.check("with the feature off, dragging on canvas no longer rotates", not sim.changes(s)["rotated"])
    s = sim.snapshot()
    yield from sim.drag(model(), offset(model(), 90, 50), button='RIGHTMOUSE')
    yield from sim.key('ESC')  # closes Blender's own right-click menu
    sim.check("with the feature off, right-drag no longer rotates", not sim.changes(s)["rotated"])
    sim.prefs.use_zbrush_keys = True
    yield 0.3
    s = sim.snapshot()
    yield from sim.drag(canvas, canvas_to)
    sim.check("switching it back on restores canvas rotation", sim.changes(s)["rotated"])


simlib.run("sim_zbrush_keys", test)
