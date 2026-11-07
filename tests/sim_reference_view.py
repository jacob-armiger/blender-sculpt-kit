"""Interactive test: the reference view puts the reference on the left and the sculpt on the right,
keeps the two on the same view whatever is done to either, still sculpts, and closes cleanly."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

import simlib  # noqa: E402


def close(a, b, tolerance=1e-4):
    return float(np.abs(np.array(a) - np.array(b)).max()) < tolerance


def shown(ob, space):
    """Whether this viewport shows the object, local collections included."""
    return ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).visible_in_viewport_get(space)


def eye_closed(collection):
    """Whether the collection is hidden by its eye in the Outliner."""
    layers = [bpy.context.view_layer.layer_collection]
    while layers:
        layer = layers.pop()
        if layer.collection == collection:
            return layer.hide_viewport
        layers.extend(layer.children)
    return None


def test(sim):
    module = importlib.import_module(simlib.MODULE + ".features.reference_view")
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    with sim.override():
        bpy.ops.sculptkit.new_sculpt(frame=True)
    yield 0.8
    sculpt_ob = bpy.context.active_object
    sculpt_name = sculpt_ob.name
    scene = bpy.context.scene

    # Something to look at in the reference view.
    reference = bpy.data.collections.new("Reference")
    scene.collection.children.link(reference)
    cube_mesh = bpy.data.meshes.new("RefCube")
    cube_mesh.from_pydata([(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)],
                          [], [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    cube = bpy.data.objects.new("RefCube", cube_mesh)
    reference.objects.link(cube)
    yield 0.3

    original = sim.area.as_pointer()
    panels_before = (sim.space().show_region_ui, sim.space().show_region_toolbar)
    view_before = sim.rv3d().view_matrix.copy()
    sim.check("starts with one viewport", len(sim.view3d_areas()) == 1)

    def together(left, right):
        a, b = sim.rv3d(left), sim.rv3d(right)
        return (close(a.view_matrix, b.view_matrix) and a.view_perspective == b.view_perspective
                and a.is_perspective == b.is_perspective)

    def scale(area):
        """Pixels that half a unit at the view's centre covers, and how far that centre is from the canvas's."""
        rv3d = sim.rv3d(area)
        centre = Vector(sim.project(rv3d.view_location, area))
        up = Vector(sim.project(rv3d.view_location + rv3d.view_rotation @ Vector((0.0, 0.5, 0.0)), area))
        return (up - centre).length, (centre - Vector(sim.at(0.5, 0.5, area))).length

    # ---------------------------------------------------------------- open
    with sim.override():
        result = bpy.ops.sculptkit.reference_view()
    yield 1.0
    areas = sim.view3d_areas()
    sim.check("opening splits the viewport in two", result == {'FINISHED'} and len(areas) == 2, len(areas))
    if len(areas) != 2:
        return
    left, right = areas
    sim.check("the reference is on the left, alone", shown(cube, sim.space(left)) and not shown(sculpt_ob, sim.space(left)))
    sim.check("the sculpt is on the right, without the reference",
              shown(sculpt_ob, sim.space(right)) and not shown(cube, sim.space(right)))
    active_collection = bpy.context.view_layer.active_layer_collection.collection
    sim.check("new objects go to the sculpt's side, not into the reference collection",
              active_collection != bpy.context.scene.sculpt_kit_reference, active_collection.name)
    # Clicking a reference in the Outliner makes the reference collection the one new objects go to.
    for layer in bpy.context.view_layer.layer_collection.children:
        if layer.collection == bpy.context.scene.sculpt_kit_reference:
            bpy.context.view_layer.active_layer_collection = layer
    for area in areas:
        area.tag_redraw()
    yield 0.5
    sim.check("while the view is open, the reference collection does not stay the one new objects go to",
              bpy.context.view_layer.active_layer_collection.collection != bpy.context.scene.sculpt_kit_reference,
              bpy.context.view_layer.active_layer_collection.collection.name)
    with sim.override(right):
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.mesh.primitive_ico_sphere_add(radius=0.2, location=(2.0, 0.0, 0.0))
    added = bpy.context.active_object
    sim.check("(the added object is the new one)", added.name.startswith("Icosphere"), added.name)
    yield 0.3
    sim.check("one added with Blender's Add menu shows on the sculpt's side", shown(added, sim.space(right))
              and not shown(added, sim.space(left)), [c.name for c in added.users_collection])
    added_mesh = added.data
    bpy.data.objects.remove(added)
    bpy.data.meshes.remove(added_mesh)
    sculpt_ob, cube = bpy.data.objects[sculpt_name], bpy.data.objects["RefCube"]
    sculpt_ob.select_set(True)
    bpy.context.view_layer.objects.active = sculpt_ob
    with sim.override(right):
        bpy.ops.object.mode_set(mode='SCULPT')
    yield 0.3
    panels = (sim.space(right).show_region_ui, sim.space(right).show_region_toolbar)
    sim.check("the viewport you were working in stays the sculpt's, panels and overlays as they were",
              right.as_pointer() == original and panels == panels_before and sim.space(right).overlay.show_overlays,
              (right.as_pointer() == original, panels, panels_before))
    sim.check("the reference half is bare", not sim.space(left).overlay.show_overlays and not sim.space(left).show_region_ui
              and not sim.space(left).show_region_toolbar)
    toolbar_visible = any(region.type == 'TOOLS' and region.width > 1 and region.height > 1
                          for region in right.regions)
    reference_bands = module.bands(sim.window.screen, left)
    sim.check("matching edges leaves a blank strip beside the reference when the sculpt toolbar is visible",
              not toolbar_visible or any(x0 == 0 and x1 > 0 for x0, _y0, x1, _y1 in reference_bands),
              reference_bands)
    sim.check("the picture did not jump when the view opened", close(sim.rv3d(right).view_matrix, view_before)
              and together(left, right))
    from bl_ext.user_default.sculpt_kit.features.zbrush_look import material
    left_shading, right_shading = sim.space(left).shading, sim.space(right).shading
    shading_before = module._shading(right)
    key = sim.window.screen.as_pointer()

    def step(area):
        """Draw-time check on one half, then the sync it schedules for after the draw, run now."""
        with sim.override(area):
            module._keep_in_step()
        if bpy.app.timers.is_registered(module._match_shading):
            bpy.app.timers.unregister(module._match_shading)
            module._match_shading()

    def alike():
        return module._shading(left) == module._shading(right)

    available = {light.name for light in bpy.context.preferences.studio_lights if light.type == 'MATCAP'}
    choices = [name for name in material.MATCAPS.values() if name in available]
    if len(choices) >= 2:
        first, second = choices[:2]
        left_shading.type = right_shading.type = 'SOLID'
        left_shading.light = right_shading.light = 'MATCAP'
        left_shading.studio_light = right_shading.studio_light = first
        module._shadings[key] = module._shading(right)
        right_shading.studio_light = second
        step(left)
        sim.check("choosing a matcap in the sculpt half syncs it to the reference half",
                  left_shading.studio_light == second and right_shading.studio_light == second,
                  (left_shading.studio_light, right_shading.studio_light))
        left_shading.studio_light = first
        step(right)
        sim.check("choosing a matcap in the reference half syncs it to the sculpt half",
                  left_shading.studio_light == first and right_shading.studio_light == first,
                  (left_shading.studio_light, right_shading.studio_light))

        studios = [light.name for light in bpy.context.preferences.studio_lights if light.type == 'STUDIO']
        right_shading.light = 'STUDIO'
        step(left)
        sim.check("switching the sculpt half to studio lighting switches the reference half",
                  left_shading.light == 'STUDIO' and alike(), (left_shading.light, left_shading.studio_light))
        if len(studios) >= 2:
            other = next(name for name in studios if name != right_shading.studio_light)
            right_shading.studio_light = other
            right_shading.use_world_space_lighting = True
            right_shading.studiolight_rotate_z = 0.7
            step(left)
            sim.check("and the studio light picked, its rotation and world-space lighting follow",
                      left_shading.studio_light == other and left_shading.use_world_space_lighting
                      and abs(left_shading.studiolight_rotate_z - 0.7) < 1e-4,
                      (left_shading.studio_light, left_shading.studiolight_rotate_z))
            left_shading.studio_light = studios[0] if studios[0] != other else studios[1]
            step(right)
            sim.check("choosing a studio light in the reference half syncs it to the sculpt half",
                      right_shading.studio_light == left_shading.studio_light, right_shading.studio_light)

        # The rest of the Shading popover, changed in both halves at once: each change reaches the other half.
        right_shading.show_cavity, right_shading.cavity_type = True, 'BOTH'
        right_shading.cavity_ridge_factor = 1.7
        right_shading.background_type, right_shading.background_color = 'VIEWPORT', (0.1, 0.1, 0.3)
        left_shading.color_type, right_shading.color_type = 'MATERIAL', 'VERTEX'
        left_shading.show_shadows, left_shading.shadow_intensity = True, 0.3
        left_shading.show_specular_highlight = not right_shading.show_specular_highlight
        step(left)
        sim.check("cavity and background set in the sculpt half, shadows and specular in the reference half, all "
                  "reach both", alike() and left_shading.show_cavity and abs(left_shading.cavity_ridge_factor - 1.7) < 1e-4
                  and left_shading.background_type == 'VIEWPORT' and right_shading.show_shadows
                  and abs(right_shading.shadow_intensity - 0.3) < 1e-4,
                  [n for n, a, b in zip(module._shading_settings(), module._shading(left), module._shading(right))
                   if a != b])
        sim.check("but each half keeps its own object colour", left_shading.color_type == 'MATERIAL'
                  and right_shading.color_type == 'VERTEX', (left_shading.color_type, right_shading.color_type))

        right_shading.type = 'MATERIAL'
        right_shading.studiolight_intensity = 1.6
        right_shading.studiolight_background_alpha = 0.4
        step(left)
        sim.check("Material Preview, with its light's strength and the world's opacity, follows too",
                  left_shading.type == 'MATERIAL' and abs(left_shading.studiolight_intensity - 1.6) < 1e-4
                  and abs(left_shading.studiolight_background_alpha - 0.4) < 1e-4 and alike(),
                  (left_shading.type, left_shading.studiolight_intensity))
        left_shading.type = 'SOLID'
        left_shading.light = 'MATCAP'
        step(right)
        sim.check("going back to Solid and a matcap in either half brings the other back to it",
                  right_shading.type == 'SOLID' and right_shading.light == 'MATCAP' and alike(),
                  (right_shading.type, right_shading.light))
        for area in (left, right):
            module._shade(area, shading_before)
        module._shadings[key] = shading_before
    sim.check("no helper objects were added", not [ob for ob in bpy.data.objects if ob.type == 'CAMERA'],
              [ob.name for ob in bpy.data.objects])
    sim.screenshot("reference_view_open.png")

    # ---------------------------------------------------------- stays in sync
    view = sim.rv3d(right).view_matrix.copy()
    start = sim.at(0.9, 0.88, right)
    yield from sim.drag(start, (start[0] - 60, start[1] - 80), button='RIGHTMOUSE')
    yield 0.4
    sim.check("turning the sculpt half turns the reference with it", not close(sim.rv3d(right).view_matrix, view)
              and together(left, right))

    view = sim.rv3d(left).view_matrix.copy()
    start = sim.at(0.5, 0.5, left)
    yield from sim.drag(start, (start[0] + 70, start[1] + 40), button='MIDDLEMOUSE')
    yield 0.4
    sim.check("turning the reference half turns the sculpt with it", not close(sim.rv3d(left).view_matrix, view)
              and together(left, right))

    sim.check("both start in perspective", sim.rv3d(left).is_perspective and sim.rv3d(right).is_perspective)
    yield from sim.key('P', at=sim.at(0.5, 0.5, right))
    yield 0.5
    sim.check("switching perspective off in the sculpt half switches it off in the reference too",
              not sim.rv3d(right).is_perspective and not sim.rv3d(left).is_perspective and together(left, right),
              (sim.rv3d(left).view_perspective, sim.rv3d(right).view_perspective))
    view = sim.rv3d(right).view_matrix.copy()
    start = sim.at(0.9, 0.88, right)
    yield from sim.drag(start, (start[0] + 50, start[1] - 60), button='RIGHTMOUSE')
    yield 0.4
    sim.check("and they still turn together afterwards", not close(sim.rv3d(right).view_matrix, view)
              and together(left, right) and not sim.rv3d(left).is_perspective)
    yield from sim.key('P', at=sim.at(0.5, 0.5, left))
    yield 0.5
    sim.check("switching it back on from the reference half brings both back",
              sim.rv3d(right).is_perspective and sim.rv3d(left).is_perspective and together(left, right),
              (sim.rv3d(left).view_perspective, sim.rv3d(right).view_perspective))

    with sim.override(right):
        bpy.ops.view3d.view_axis(type='FRONT')
    yield 1.0
    front = sim.rv3d(right).view_rotation @ Vector((0.0, 0.0, -1.0))
    sim.check("a front view in one is a front view in both", close(front, (0.0, 1.0, 0.0), 1e-3) and together(left, right)
              and sim.rv3d(left).is_orthographic_side_view == sim.rv3d(right).is_orthographic_side_view,
              tuple(round(v, 3) for v in front))
    yield from sim.drag(sim.at(0.9, 0.88, right), sim.at(0.82, 0.8, right), button='RIGHTMOUSE')
    yield 0.4
    with sim.override(left):
        bpy.ops.view3d.view_all()
    yield 1.0
    sim.check("framing everything from the reference half reframes the sculpt half", together(left, right))

    sim.space(right).lens = 85.0
    yield 0.5
    (size_left, off_left), (size_right, off_right) = scale(left), scale(right)
    sim.check("the model is drawn the same size in both, at the middle of each", abs(size_left - size_right) < 0.01 * size_right
              and off_left < 2.0 and off_right < 2.0,
              "%.1f px and %.1f px; lenses %.1f and %.1f" % (size_left, size_right, sim.space(left).lens, sim.space(right).lens))
    sim.space(right).lens = 50.0
    yield 0.5
    sim.screenshot("reference_view_synced.png")

    # ---------------------------------------------- sculpting, and undoing it
    # Everything so far was done by script, which records no undo steps. Record one, as a click would have.
    with sim.override(right):
        bpy.ops.ed.undo_push(message="Reference view test set-up")
    yield 0.3
    before = sim.positions(sculpt_ob)
    centre = sim.project(sculpt_ob.matrix_world.translation, right)
    yield from sim.drag(centre, (centre[0] + 60, centre[1] + 20))
    sim.check("sculpting works in the right half", float(np.abs(sim.positions(sculpt_ob) - before).max()) > 1e-5)
    yield from sim.drag(sim.at(0.9, 0.88, right), sim.at(0.84, 0.8, right), button='RIGHTMOUSE')
    yield 0.3
    yield from sim.undo()
    sculpt_ob, cube = bpy.data.objects[sculpt_name], bpy.data.objects["RefCube"]  # undo can replace the objects
    sim.check("undo takes back the stroke, not the turn of the view that came after it",
              close(sim.positions(sculpt_ob), before, 1e-6) and together(left, right))

    # ------------------------------------------- as it is after a file is reloaded
    module._halves_of.clear()
    module._shown.clear()
    view = sim.rv3d(right).view_matrix.copy()
    yield from sim.drag(sim.at(0.9, 0.88, right), sim.at(0.8, 0.8, right), button='RIGHTMOUSE')
    yield 0.4
    sim.check("a view saved open carries on in step when its file is opened again",
              module.is_open(sim.window.screen) and not close(sim.rv3d(right).view_matrix, view) and together(left, right))

    # --------------------------------------------------------------- close
    view_expected = sim.rv3d(right).view_matrix.copy()
    with sim.override(right):
        result = bpy.ops.sculptkit.reference_view()
    yield 1.0
    areas = sim.view3d_areas()
    sim.check("closing leaves one viewport, the sculpt's", result == {'FINISHED'} and len(areas) == 1
              and areas[0].as_pointer() == original, len(areas))
    space, rv3d = sim.space(), sim.rv3d()
    sim.check("it is an ordinary view again", not space.use_local_collections and not module.is_open(sim.window.screen)
              and "sculpt_kit_reference_view" not in sim.window.screen)
    sim.check("the picture did not jump when the view closed", close(rv3d.view_matrix, view_expected))
    sim.check("the viewport goes on showing the sculpt without the reference, which its eye hides",
              shown(sculpt_ob, space) and not shown(cube, space) and eye_closed(cube.users_collection[0]))
    sim.screenshot("reference_view_closed.png")

    # ------------------------------------ the reference half closed with Blender's own Close Area
    with sim.override():
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    left, right = sim.view3d_areas()
    with sim.override(left):
        bpy.ops.screen.area_close()
    yield 1.0
    areas = sim.view3d_areas()
    space = sim.space()
    sim.check("closing the reference half by hand leaves an ordinary viewport, the reference hidden by its eye",
              len(areas) == 1 and not space.use_local_collections and "sculpt_kit_reference_view" not in sim.window.screen
              and shown(sculpt_ob, space) and not shown(cube, space) and eye_closed(cube.users_collection[0]),
              (len(areas), space.use_local_collections))
    with sim.override():
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    left, right = sim.view3d_areas()
    sim.check("and the button opens the view again, the reference showing in its half and not the sculpt's",
              module.is_open(sim.window.screen) and shown(cube, sim.space(left)) and not shown(cube, sim.space(right))
              and shown(sculpt_ob, sim.space(right)) and not eye_closed(cube.users_collection[0]))
    with sim.override(sim.view3d_areas()[1]):
        bpy.ops.sculptkit.reference_view()
    yield 1.0

    # ------------------------------ a sculpt shown by Attribute colour does not colour the reference
    sim.space().shading.color_type = 'VERTEX'
    with sim.override():
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    left, right = sim.view3d_areas()
    sim.check("with the sculpt in Attribute colour, the reference half opens in Material and the sculpt stays as it was",
              sim.space(left).shading.color_type == 'MATERIAL' and sim.space(right).shading.color_type == 'VERTEX',
              (sim.space(left).shading.color_type, sim.space(right).shading.color_type))
    with sim.override(right):
        bpy.ops.sculptkit.reference_view()
    yield 1.0
    sim.space().shading.color_type = 'MATERIAL'


simlib.run("sim_reference_view", test)
