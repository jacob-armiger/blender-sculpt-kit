"""Interactive test: Ctrl + R remeshes by resolution, the mask survives, and the DynaMesh gesture is opt-in."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402

import simlib  # noqa: E402


def sphere_faces(resolution):
    """Faces a sphere ends up with when its diameter spans this many voxels.

    Its area in voxel faces is pi * resolution^2, and the voxel remesher makes
    about 1.5 quads per voxel face of area on a curved surface.
    """
    return 1.5 * math.pi * resolution ** 2


def test(sim):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    with sim.override():
        bpy.ops.sculptkit.new_sculpt(frame=True)
    yield 0.8
    ob = bpy.context.active_object
    scene = bpy.context.scene

    def faces():
        return len(ob.data.polygons)

    def near(count, resolution):
        return abs(count - sphere_faces(resolution)) < 0.2 * sphere_faces(resolution)

    def model():
        return sim.project((0.0, 0.0, 0.0))

    def canvas_box(**mods):
        return sim.drag(sim.at(0.08, 0.42), sim.at(0.15, 0.58), ctrl=True, **mods)

    def paint_mask():
        return sim.drag((model()[0], model()[1] + 120), (model()[0] + 120, model()[1] + 140), ctrl=True)

    # ------------------------------------------------------------- Ctrl + R
    scene.sculpt_kit_remesh_resolution = 64
    yield from sim.key('R', ctrl=True)
    yield 0.8
    sim.check("Ctrl + R remeshes to the panel's resolution", near(faces(), 64),
              "%d faces, about %d expected" % (faces(), sphere_faces(64)))

    yield from paint_mask()
    sim.check("(a mask is painted)", sim.mask().max() > 0.5)
    scene.sculpt_kit_remesh_resolution = 96
    yield from sim.key('R', ctrl=True)
    yield 0.8
    sim.check("the mask survives a remesh", near(faces(), 96) and sim.mask().max() > 0.5,
              "%d faces, max mask %.2f" % (faces(), sim.mask().max()))
    sim.check("it stays in Sculpt Mode", bpy.context.mode == 'SCULPT', bpy.context.mode)

    # ---------------------------------------- the DynaMesh gesture is opt-in
    yield from canvas_box()
    sim.check("(the canvas box clears the mask)", sim.mask().max() < 1e-4)
    before = faces()
    yield from canvas_box()
    yield 0.5
    sim.check("by default, a Ctrl + drag box on empty canvas never remeshes", faces() == before, faces())

    sim.prefs.remesh_canvas_gesture = True
    scene.sculpt_kit_remesh_resolution = 48
    yield from canvas_box()
    yield 0.8
    sim.check("with the DynaMesh gesture on, the same box remeshes when nothing is masked", near(faces(), 48),
              "%d faces, about %d expected" % (faces(), sphere_faces(48)))

    yield from paint_mask()
    before = faces()
    yield from canvas_box()
    yield 0.5
    sim.check("and it only clears the mask when something is masked", faces() == before and sim.mask().max() < 1e-4,
              "%d faces, max mask %.2f" % (faces(), sim.mask().max()))
    sim.prefs.remesh_canvas_gesture = False

    # ---------------------------------------------------------- quad remesh
    scene.sculpt_kit_quad_faces = 3000
    with sim.override():
        result = bpy.ops.sculptkit.quad_remesh(use_symmetry=False)
    yield 0.5
    quads = sum(len(p.vertices) == 4 for p in ob.data.polygons) / max(1, faces())
    sim.check("Quad Remesh turns the sculpt into quads at the panel's face count",
              result == {'FINISHED'} and quads > 0.99 and 1500 < faces() < 4500 and bpy.context.mode == 'SCULPT',
              "%d faces, %.0f%% quads, %s" % (faces(), 100 * quads, bpy.context.mode))


simlib.run("sim_remesh", test)
