"""Interactive test: the Move brush drags the far side of the model too; Blender's Grab does not."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

import simlib  # noqa: E402

MOVE = dict(asset_library_type='CUSTOM', asset_library_identifier="Sculpt Kit",
            relative_asset_identifier="SculptKit_Brushes.blend/Brush/SculptKit_Move")
GRAB = dict(asset_library_type='ESSENTIALS',
            relative_asset_identifier="brushes/essentials_brushes-mesh_sculpt.blend/Brush/Grab")


def test(sim):
    sculpt = bpy.context.tool_settings.sculpt

    def far_and_near_movement(brush_asset, expected_name):
        """Drag sideways from the middle of a fresh sphere; return how far the near and far sides moved."""
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob)
        with sim.override():
            bpy.ops.sculptkit.new_sculpt(frame=True)
        yield 0.8
        with sim.override():
            bpy.ops.brush.asset_activate(**brush_asset)
        yield 0.3
        sim.check("%s is the active brush" % expected_name, sculpt.brush is not None and sculpt.brush.name == expected_name,
                  sculpt.brush and sculpt.brush.name)
        sculpt.unified_paint_settings.use_unified_size = True
        sculpt.unified_paint_settings.size = 260  # pixels across: well inside the sphere's outline
        before = sim.positions()
        looking = sim.rv3d().view_rotation @ Vector((0.0, 0.0, -1.0))
        depth = before @ np.array(looking)  # negative = the side facing the viewer
        centre = sim.project((0.0, 0.0, 0.0))
        yield from sim.drag(centre, (centre[0] + 150, centre[1]))
        moved = np.linalg.norm(sim.positions() - before, axis=1)
        result["near"] = float(moved[depth < -0.6].max())
        result["far"] = float(moved[depth > 0.6].max())

    result = {}
    yield from far_and_near_movement(MOVE, "SculptKit_Move")
    move = dict(result)
    sim.check("Move drags the side facing you", move["near"] > 0.05, "moved %.3f" % move["near"])
    sim.check("Move drags the far side just as much", move["far"] > 0.8 * move["near"],
              "far %.3f vs near %.3f" % (move["far"], move["near"]))

    yield from far_and_near_movement(GRAB, "Grab")
    grab = dict(result)
    sim.check("Blender's own Grab leaves the far side behind", grab["near"] > 0.02 and grab["far"] < 0.05 * grab["near"],
              "far %.4f vs near %.3f" % (grab["far"], grab["near"]))


simlib.run("sim_move_brush", test)
