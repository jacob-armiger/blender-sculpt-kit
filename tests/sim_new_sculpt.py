"""Interactive test: a New Sculpt sphere is the size of Blender's starting cube and takes its place;
one added to a sculpt goes where you are looking; the view only moves when it does not show the sphere."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

import simlib  # noqa: E402


def close(a, b, tolerance=1e-4):
    return float(np.abs(np.array(a) - np.array(b)).max()) < tolerance


def test(sim):
    rv3d = sim.rv3d()

    def active():
        return bpy.context.active_object.name, bpy.context.mode

    def meshes():
        return sorted(ob.name for ob in bpy.context.view_layer.objects if ob.type == 'MESH')

    def on_screen():
        return sorted(ob.name for ob in bpy.context.view_layer.objects if ob.type == 'MESH' and ob.visible_get())

    def across(ob, size):
        return all(abs(d - size) < 1e-3 for d in ob.dimensions)

    def in_the_middle(ob, area=None):
        return (Vector(sim.project(ob.location, area)) - Vector(sim.at(0.5, 0.5, area))).length < 3

    def shown(ob, space):
        return ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).visible_in_viewport_get(space)

    def only(*names):
        """Leave Sculpt Mode and hide every mesh but these."""
        if bpy.context.mode != 'OBJECT':
            with sim.override():
                bpy.ops.object.mode_set(mode='OBJECT')
        for ob in bpy.context.view_layer.objects:
            if ob.type == 'MESH':
                ob.hide_set(ob.name not in names)

    def look(location, distance):
        rv3d.view_location = location
        rv3d.view_distance = distance

    # --------------------------------------------- Blender's starting scene
    sim.check("the scene starts with Blender's cube", on_screen() == ["Cube"] and active() == ("Cube", 'OBJECT'), on_screen())
    view = rv3d.view_matrix.copy()
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    first = bpy.context.active_object.name
    sim.check("the sphere is the size of the cube and takes its place: the cube is hidden",
              on_screen() == [first] and across(bpy.data.objects[first], 2.0)
              and close(bpy.data.objects[first].location, (0, 0, 0)), (on_screen(), tuple(bpy.data.objects[first].dimensions)))
    sim.check("in Sculpt Mode, with the view exactly where it was", bpy.context.mode == 'SCULPT'
              and close(rv3d.view_matrix, view, 1e-6), bpy.context.mode)
    sim.screenshot("new_sculpt_first.png")
    yield from sim.undo()
    sim.check("undo brings the cube back and takes the sphere away", on_screen() == ["Cube"] and meshes() == ["Cube"]
              and active() == ("Cube", 'OBJECT'), (on_screen(), active()))
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    first = bpy.context.active_object.name

    # ------------------------------------------------- added to a sculpt
    yield from sim.drag(sim.at(0.9, 0.88), sim.at(0.8, 0.75), button='RIGHTMOUSE')
    yield 0.4
    sim.check("the view was turned, as it is while sculpting", not close(rv3d.view_matrix, view))
    view = rv3d.view_matrix.copy()
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    part = bpy.context.active_object
    sim.check("adding a sphere to a sculpt leaves the view exactly where it was", close(rv3d.view_matrix, view, 1e-6))
    sim.check("it is the same size", across(part, 2.0), tuple(part.dimensions))
    sim.check("it sits on the sculpt's surface, in the middle of the view", abs(part.location.length - 1.0) < 0.01
              and in_the_middle(part), (round(part.location.length, 4), sim.project(part.location), sim.at(0.5, 0.5)))
    sim.check("Sculpt Mode moved over to it", active() == (part.name, 'SCULPT') and bpy.data.objects[first].mode == 'OBJECT'
              and on_screen() == sorted([first, part.name]), (active(), on_screen()))
    sim.screenshot("new_sculpt_added.png")

    before = sim.positions(part)
    centre = sim.project(part.location)
    yield from sim.drag(centre, (centre[0] + 30, centre[1] + 12))
    sim.check("and sculpting works on it", float(np.abs(sim.positions(part) - before).max()) > 1e-5)
    yield from sim.undo()
    yield from sim.undo()
    sim.check("undo takes the stroke back, then the sphere, and returns to the sculpt",
              on_screen() == [first] and active() == (first, 'SCULPT') and close(rv3d.view_matrix, view, 1e-6),
              (on_screen(), active()))

    rv3d.view_location = rv3d.view_location + rv3d.view_rotation @ Vector((3.0, 0.0, 0.0))
    yield 0.5
    view = rv3d.view_matrix.copy()
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    beside = bpy.context.active_object
    # To within a pixel of it: the middle of a canvas an odd number of pixels wide falls between two pixels.
    sim.check("with the sculpt off to the side, the sphere goes to the point the view turns about",
              close(beside.location, rv3d.view_location, 5e-3) and in_the_middle(beside) and across(beside, 2.0)
              and close(rv3d.view_matrix, view, 1e-6), (tuple(beside.location), tuple(rv3d.view_location)))
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    again = bpy.context.active_object
    sim.check("the next one lands on that sphere's surface, the same size again",
              across(again, 2.0) and abs((again.location - beside.location).length - 1.0) < 0.01,
              (tuple(again.dimensions), round((again.location - beside.location).length, 4)))

    sim.call(bpy.ops.sculptkit.new_sculpt, density='6', radius=0.4, location=(0.0, 3.0, 0.0), frame=True)
    yield 1.0
    given = bpy.context.active_object
    sim.check("a radius, a location and framing that are asked for are what you get", across(given, 0.8)
              and close(given.location, (0, 3, 0)) and close(rv3d.view_location, (0, 3, 0), 1e-3),
              (tuple(given.dimensions), tuple(given.location), tuple(rv3d.view_location)))

    # ------------------------------------------- when the view moves, and when not
    only()
    look((0.0, 0.0, 0.0), 12.0)
    yield 0.5
    view = rv3d.view_matrix.copy()
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    sim.check("with nothing on screen and the view on the spot, the view is left alone",
              close(bpy.context.active_object.location, (0, 0, 0)) and across(bpy.context.active_object, 2.0)
              and close(rv3d.view_matrix, view, 1e-6))
    only()
    look((0.0, 0.0, 40.0), 5.0)
    yield 0.5
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    sim.check("with the view looking somewhere else, it is framed", close(rv3d.view_location, (0, 0, 0), 1e-3),
              tuple(rv3d.view_location))
    only()
    look((0.0, 0.0, 0.0), 400.0)
    yield 0.5
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    fresh = bpy.context.active_object
    sim.check("with the view too far out to work on it, it is framed", rv3d.view_distance < 20.0
              and close(fresh.location, (0, 0, 0)), rv3d.view_distance)

    # ------------------------------------- beside a reference, in the reference view
    reference = bpy.data.collections.new("Reference")
    bpy.context.scene.collection.children.link(reference)
    mesh = bpy.data.meshes.new("RefCube")
    mesh.from_pydata([(-2, -2, -2), (2, -2, -2), (2, 2, -2), (-2, 2, -2), (-2, -2, 2), (2, -2, 2), (2, 2, 2), (-2, 2, 2)],
                     [], [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    cube = bpy.data.objects.new("RefCube", mesh)
    reference.objects.link(cube)
    yield 0.3
    with sim.override():
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    left, right = sim.view3d_areas()
    view = sim.rv3d(right).view_matrix.copy()
    sim.call(bpy.ops.sculptkit.new_sculpt, area=right, density='6')
    yield 1.0
    part = bpy.context.active_object
    sim.check("a reference that surrounds the sculpt is not in the way",
              abs(part.location.length - 1.0) < 0.01 and across(part, 2.0) and in_the_middle(part, right)
              and close(sim.rv3d(right).view_matrix, view, 1e-6),
              (round(part.location.length, 4), tuple(part.dimensions)))
    sim.check("the sphere shows in the sculpt half only", shown(part, sim.space(right)) and not shown(part, sim.space(left)))
    with sim.override(right):
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    rv3d = sim.rv3d()

    # ------------------------------------------------------------------- Solo
    with sim.override():
        bpy.ops.view3d.localview(frame_selected=False)
    yield 0.6
    space = sim.space()
    sim.check("Solo shows the active sculpt alone", space.local_view is not None and shown(part, space)
              and not shown(fresh, space))
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    solo = bpy.context.active_object
    sim.check("a sphere added while Solo is on can be seen", shown(solo, space) and across(solo, 2.0)
              and active() == (solo.name, 'SCULPT'), tuple(solo.dimensions))
    with sim.override():
        bpy.ops.view3d.localview(frame_selected=False)
    yield 0.6

    # ------------------------------------------- taking over from an eye that is off
    was = solo.name
    sim.call(bpy.ops.sculptkit.subtool_show, name=was)
    yield 0.3
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    sim.check("a sculpt whose eye is off hides when a new sphere takes over from it",
              not bpy.data.objects[was].visible_get() and bpy.context.active_object.name != was
              and bpy.context.mode == 'SCULPT', bpy.data.objects[was].visible_get())

    # ---------------------------------------------------- cubes that stay put
    only("RefCube")
    look((0.0, 0.0, 0.0), 20.0)
    yield 0.5
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6')
    yield 1.0
    sphere = bpy.context.active_object
    sim.check("a cube of your own is not hidden: the sphere is added to it", cube.visible_get()
              and abs(max(abs(v) for v in sphere.location) - 2.0) < 0.01, tuple(sphere.location))
    only("Cube")
    yield 0.5
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6', hide_cube=False)
    yield 1.0
    sphere = bpy.context.active_object
    sim.check("with Hide Starting Cube off, Blender's cube stays and the sphere is added to it",
              bpy.data.objects["Cube"].visible_get() and abs(max(abs(v) for v in sphere.location) - 1.0) < 0.01
              and in_the_middle(sphere), tuple(sphere.location))


simlib.run("sim_new_sculpt", test)
