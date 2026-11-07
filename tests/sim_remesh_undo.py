"""Interactive test: undo steps back through a fRemesher run in Sculpt Mode one step at a time, and keeps the strokes
made before it."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402

import simlib  # noqa: E402


def test(sim):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    sim.call(bpy.ops.sculptkit.new_sculpt, frame=True)  # with its undo step: undoing the first stroke lands on it
    yield 0.8

    def state():
        mesh = bpy.context.active_object.data
        co = sim.positions(bpy.context.active_object)
        return len(mesh.vertices), round(float(np.abs(co).sum()), 3), bpy.context.active_object.mode

    def stroke(dy):
        centre = sim.project((0.0, 0.0, 0.0))
        return sim.drag((centre[0] - 40, centre[1] + dy), (centre[0] + 40, centre[1] + dy))

    start = state()
    yield from stroke(0)
    first = state()
    yield from stroke(30)
    second = state()
    sim.check("two strokes changed the sculpt", start[1] != first[1] != second[1], (start, first, second))
    sim.call(bpy.ops.sculptkit.quad_remesh, target_faces=2000)
    yield 1.5
    remeshed = state()
    sim.check("the remesh ran", remeshed[0] != second[0], remeshed)
    yield from stroke(-30)
    after = state()
    sim.note("states: start %s first %s second %s remeshed %s after %s" % (start, first, second, remeshed, after))

    yield from sim.undo()
    sim.check("undo takes back the stroke on the remesh", state() == remeshed, state())
    yield from sim.undo()
    sim.check("undo takes back the remesh, keeps the strokes before it and stays in Sculpt Mode", state() == second, state())
    yield from sim.undo()  # the step saved before the remesh and the last stroke are the same state
    sim.check("one more undo leaves the same sculpt", state() == second, state())
    yield from sim.undo()
    sim.check("undo takes back the second stroke", state() == first, state())
    yield from sim.undo()
    sim.check("undo takes back the first stroke", state() == start, state())

    def redo():
        yield from sim.key('Z', oskey=sys.platform == "darwin", ctrl=sys.platform != "darwin", shift=True)
        yield 0.5

    yield from redo()
    yield from redo()
    sim.check("redo brings the strokes back", state() == second, state())
    yield from redo()
    yield from redo()
    sim.check("redo brings the remesh back", state() == remeshed, state())
    yield from redo()
    sim.check("redo brings the stroke on the remesh back", state() == after, state())

    # ------------------------------------------- a change in the corner panel after the remesh
    sim.call(bpy.ops.sculptkit.new_sculpt, frame=True)
    yield 0.8
    unstroked = state()
    yield from stroke(0)
    stroked = state()
    sim.call(bpy.ops.sculptkit.quad_remesh, target_faces=2000)
    yield 1.5
    for seed in (1, 2, 3):  # three changes in the corner panel
        bpy.context.window_manager.operators[-1].properties.seed = seed
        with sim.override():
            bpy.ops.ed.undo_redo('EXEC_DEFAULT', True)  # what a change in the corner panel runs
        yield 1.5
    sim.check("a rerun from the corner panel remeshes again", state()[0] != stroked[0], state())
    yield from sim.undo()
    sim.check("undo after reruns goes back to the strokes, in one press", state() == stroked, state())
    yield from sim.undo()  # the step saved before the remesh and the stroke are the same state
    yield from sim.undo()
    sim.check("reruns leave no extra undo steps: two more presses take the stroke back", state() == unstroked, state())

    # ------------------------------------------------------------------------- Voxel Remesh
    yield from stroke(30)
    stroked = state()
    sim.call(bpy.ops.sculptkit.voxel_remesh, resolution=64)
    yield 1.0
    sim.check("the voxel remesh ran", state()[0] != stroked[0], state())
    yield from stroke(-30)
    voxel_after = state()
    yield from sim.undo()
    sim.check("undo takes back a stroke on the voxel remesh", state() != voxel_after and state()[0] == voxel_after[0], state())
    yield from sim.undo()
    sim.check("undo takes back the voxel remesh and keeps the strokes before it", state() == stroked, state())

    # -------------------------------------------- a second press, after strokes on the first remesh
    sim.call(bpy.ops.sculptkit.new_sculpt, frame=True)
    yield 0.8
    sim.call(bpy.ops.sculptkit.quad_remesh, target_faces=2000)
    yield 1.5
    first_remesh = state()
    yield from stroke(0)
    stroked_remesh = state()
    sim.check("a stroke on the remesh changed it", stroked_remesh != first_remesh, stroked_remesh)
    sim.call(bpy.ops.sculptkit.quad_remesh, target_faces=2000)
    yield 1.5
    sim.check("the second press remeshed again", state() != stroked_remesh, state())
    yield from sim.undo()
    sim.check("undoing a second press keeps the stroke made since the first", state() == stroked_remesh, state())


simlib.run("sim_remesh_undo", test)
