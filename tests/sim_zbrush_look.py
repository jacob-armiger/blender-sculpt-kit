"""Interactive test: the ZBrush look's colour theme, shelf, layout, subtool list and material."""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402

import simlib  # noqa: E402

LAYOUT = "Sculpt Kit"


def close(a, b, tolerance=1e-4):
    return float(np.abs(np.array(a) - np.array(b)).max()) < tolerance


def test(sim):
    look = importlib.import_module(simlib.MODULE + ".features.zbrush_look")
    theme = look.theme
    prefs = sim.prefs
    preferences = bpy.context.preferences
    gradients = preferences.themes[0].view_3d.space.gradients
    bar = bpy.types.VIEW3D_HT_tool_header
    blender = theme.data()["blender"]

    def workspace():
        return sim.window.workspace.name

    def blender_bar():
        """Whether the brush bar is drawn by Blender's own function alone."""
        return [f.__module__ for f in bar._dyn_ui_initialize()] == ["bl_ui.space_view3d"]

    def kit_workspaces():
        return [w.name for w in bpy.data.workspaces if w.get("sculpt_kit_layout")]

    def meshes():
        return sorted(ob.name for ob in bpy.context.view_layer.objects if ob.type == 'MESH')

    def matcap(area=None):
        return sim.space(area).shading.studio_light

    def loaded(name):
        return any(light.name == name for light in preferences.studio_lights)

    def shown(image, point):
        """Brightness, 0 to 255, of a window pixel in a screenshot."""
        width = image.size[0]
        start = (int(point[1]) * width + int(point[0])) * 4
        return round(sum(image.pixels[start:start + 3]) / 3 * 255)

    def counts():
        return {kind: len(getattr(bpy.data, kind)) for kind in ("objects", "meshes", "scenes", "brushes", "cameras",
                                                                "collections", "materials", "images")}

    workspaces_at_start = sorted(w.name for w in bpy.data.workspaces)

    # ------------------------------------------- what earlier sessions saved
    saved = json.loads(prefs.look_theme_backup or "{}")
    sim.check("the ZBrush colour theme is on when Blender starts", theme.wearing() == 'ZBRUSH', theme.wearing())
    sim.check("and the way back from it was saved with it",
              len(saved) > 200 and saved.get("system.use_region_overlap") is True, len(saved))
    sim.check("the canvas fades from black to grey", gradients.background_type == 'LINEAR'
              and close(gradients.high_gradient, (0, 0, 0)) and gradients.gradient[0] > 0.25, tuple(gradients.gradient))
    sim.check("shelves sit beside the canvas", not preferences.system.use_region_overlap)

    # ------------------------------------------------------ shelf and material
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    with sim.override():
        bpy.ops.sculptkit.new_sculpt(density='6', frame=True)
    yield 1.0
    sim.check("the brush bar is the ZBrush shelf", look.shelf.in_place())
    sim.check("the viewport header has the view switches and the material picker",
              hasattr(bpy.types, "SCULPTKIT_PT_material"))
    sim.check("a new sculpt starts in red wax", matcap() == "red_wax.exr", matcap())
    sim.screenshot("look_1_zbrush_shelf.png")

    prefs.look_material_zbrush = 'GREY'
    yield 0.8
    sim.check("picking Grey moves the sculpt over to the kit's own grey", matcap() == "sculptkit_grey.exr", matcap())
    # Its lighting, measured on the sphere: what the centre, a point 60% of the way up and one 60% down show.
    centre = Vector(sim.project((0.0, 0.0, 0.0)))
    rim = Vector(sim.project(sim.rv3d().view_rotation @ Vector((0.0, 1.0, 0.0))))
    image = bpy.data.images.load(sim.screenshot("look_1b_grey.png"))
    middle, upper, lower = (shown(image, centre + (rim - centre) * f) for f in (0.0, 0.6, -0.6))
    sim.check("the grey is lit like ZBrush's: bright in front, a little lighter above, darker underneath",
              abs(middle - 168) < 20 and abs(upper - 165) < 20 and abs(lower - 92) < 20, (middle, upper, lower))
    bpy.data.images.remove(image)
    result = sim.call(bpy.ops.sculptkit.material, material='RED_WAX')
    yield 0.5
    sim.check("the picker's own button goes back to red wax, and remembers it", result == {'FINISHED'}
              and matcap() == "red_wax.exr" and prefs.look_material_zbrush == 'RED_WAX', matcap())

    # ------------------------------------------------------------------ layout
    came_from = workspace()
    view_before = sim.rv3d().view_matrix.copy()
    data_before = counts()
    with sim.override():
        result = bpy.ops.sculptkit.zbrush_layout()
    yield 2.0
    areas = list(sim.window.screen.areas)
    sim.check("ZBrush Layout switches to the Sculpt Kit workspace", result == {'FINISHED'} and workspace() == LAYOUT,
              workspace())
    sim.check("it is one big canvas", len(areas) == 1 and areas[0].type == 'VIEW_3D', [a.type for a in areas])
    tray = next(r for r in sim.area.regions if r.type == 'UI')
    sim.check("with the Sculpt Kit tray open beside it", sim.space().show_region_ui and tray.width > 100
              and tray.active_panel_category == "Sculpt Kit", (tray.width, tray.active_panel_category))
    sim.check("still in Sculpt Mode, in red wax", bpy.context.mode == 'SCULPT' and matcap() == "red_wax.exr",
              (bpy.context.mode, matcap()))
    sim.check("looking at the model from where you were", close(sim.rv3d().view_matrix, view_before, 1e-3))
    sim.check("building it added nothing else to the file", counts() == data_before,
              {k: (data_before[k], v) for k, v in counts().items() if v != data_before[k]})
    sim.screenshot("look_2_zbrush_layout.png")

    before = sim.positions()
    centre = sim.project((0.0, 0.0, 0.0))
    yield from sim.drag(centre, (centre[0] + 90, centre[1] + 30))
    sim.check("sculpting works on the big canvas", float(np.abs(sim.positions() - before).max()) > 1e-5)

    # ---------------------------------------------------------------- subtools
    # Operators are run the way a click runs them, so that each records its undo step.
    def active():
        return bpy.context.active_object.name, bpy.context.mode

    first = bpy.context.active_object.name
    sim.call(bpy.ops.sculptkit.new_sculpt, density='6', radius=0.5, location=(0.0, 0.0, 0.0))
    yield 0.8
    second = bpy.context.active_object.name
    sim.check("a second sculpt joins the subtool list", meshes() == sorted([first, second]), meshes())
    result = sim.call(bpy.ops.sculptkit.subtool_select, name=first)
    yield 0.5
    sim.check("clicking a subtool moves Sculpt Mode over to it", result == {'FINISHED'}
              and active() == (first, 'SCULPT') and bpy.data.objects[second].mode == 'OBJECT', active())
    before = sim.positions()
    centre = sim.project((0.0, 0.0, 0.0))
    yield from sim.drag(centre, (centre[0] + 60, centre[1] + 10))
    brush = bpy.context.tool_settings.sculpt.brush
    sim.check("and the stroke lands on it", float(np.abs(sim.positions() - before).max()) > 1e-5,
              "moved %.6f with %s at %s; view distance %.2f" % (float(np.abs(sim.positions() - before).max()),
                                                               brush and brush.name, centre, sim.rv3d().view_distance))
    yield from sim.undo()
    sim.check("undo takes the stroke back", close(sim.positions(), before, 1e-6))
    yield from sim.undo()
    sim.check("and then the change of subtool", active() == (second, 'SCULPT'), active())
    sim.call(bpy.ops.sculptkit.subtool_select, name=first)
    yield 0.5

    def visible(name):
        return bpy.data.objects[name].visible_get()

    def eye_off(name):
        return look.subtools.eye_off(bpy.data.objects[name], bpy.context.view_layer.objects.active)

    sim.call(bpy.ops.sculptkit.subtool_show, name=second)
    yield 0.3
    sim.check("the eye hides another subtool", not visible(second) and eye_off(second))
    sim.call(bpy.ops.sculptkit.subtool_show, name=second)
    yield 0.3
    sim.check("and shows it again", visible(second) and not eye_off(second))

    # The eye of the subtool being sculpted works as in ZBrush: it stays in view until another takes over.
    result = sim.call(bpy.ops.sculptkit.subtool_show, name=first)
    yield 0.3
    sim.check("the eye of the subtool being sculpted switches off, and it stays in view", result == {'FINISHED'}
              and visible(first) and eye_off(first) and active() == (first, 'SCULPT'), (visible(first), eye_off(first)))
    sim.call(bpy.ops.sculptkit.subtool_select, name=second)
    yield 0.5
    sim.check("it hides once another subtool is picked", not visible(first) and active() == (second, 'SCULPT'),
              (visible(first), active()))
    sim.call(bpy.ops.sculptkit.subtool_select, name=first)
    yield 0.5
    sim.check("picked again it is back in view, its eye still off", visible(first) and eye_off(first)
              and active() == (first, 'SCULPT'), (visible(first), eye_off(first), active()))
    yield from sim.undo()
    sim.check("undo goes back to the other one, with this one hidden again", not visible(first)
              and active() == (second, 'SCULPT'), (visible(first), active()))
    sim.call(bpy.ops.sculptkit.subtool_select, name=first)
    yield 0.5
    sim.call(bpy.ops.sculptkit.subtool_show, name=first)
    yield 0.3
    sim.check("its eye switches back on", visible(first) and not eye_off(first))
    sim.call(bpy.ops.sculptkit.subtool_select, name=second)
    yield 0.5
    sim.check("and then it stays in view when another is picked", visible(first) and visible(second)
              and active() == (second, 'SCULPT'), (visible(first), visible(second), active()))

    # Blender's own way of moving on to another mesh: Alt + Q with the pointer over it.
    sim.call(bpy.ops.sculptkit.subtool_show, name=second)
    yield 0.3
    yield from sim.key('Q', at=sim.project((0.0, 0.0, 0.0)), alt=True)
    yield 0.8
    sim.check("Blender's Transfer Mode counts as picking another too", active() == (first, 'SCULPT')
              and not visible(second) and not eye_off(first), (active(), visible(second)))
    sim.call(bpy.ops.sculptkit.subtool_show, name=second)
    yield 0.3
    sim.check("and the eye brings the hidden one back", visible(second) and active() == (first, 'SCULPT'))

    sim.call(bpy.ops.sculptkit.subtool_duplicate)
    yield 0.5
    copies = [name for name in meshes() if name not in (first, second)]
    sim.check("Duplicate adds a copy and stays on the original", len(copies) == 1 and active() == (first, 'SCULPT')
              and len(bpy.data.objects[copies[0]].data.vertices) == len(bpy.data.objects[first].data.vertices), meshes())
    copy = copies[0]
    sim.call(bpy.ops.sculptkit.subtool_show, name=first, others=True)
    yield 0.3
    sim.check("Shift + click on an eye hides all the others", bpy.data.objects[first].visible_get()
              and not bpy.data.objects[second].visible_get() and not bpy.data.objects[copy].visible_get())
    sim.call(bpy.ops.sculptkit.subtool_show, name=first, others=True)
    yield 0.3
    sim.check("and again shows them", bpy.data.objects[second].visible_get() and bpy.data.objects[copy].visible_get())
    with sim.override():
        bpy.ops.view3d.view_all()
    yield 0.6
    sim.screenshot("look_3_subtools.png")

    faces = len(bpy.data.objects[first].data.polygons) + len(bpy.data.objects[second].data.polygons)
    result = sim.call(bpy.ops.sculptkit.subtool_merge_down)
    yield 0.5
    sim.check("Merge Down joins the subtool below into the active one", result == {'FINISHED'}
              and meshes() == sorted([first, copy]) and len(bpy.data.objects[first].data.polygons) == faces
              and active() == (first, 'SCULPT'), meshes())
    yield from sim.undo()
    sim.check("and can be undone", meshes() == sorted([first, second, copy]) and active() == (first, 'SCULPT'), meshes())
    sim.call(bpy.ops.sculptkit.subtool_delete)
    yield 0.5
    sim.check("Delete removes the active subtool and moves on to the next", meshes() == sorted([second, copy])
              and active() == (second, 'SCULPT'), (meshes(), active()))
    yield from sim.undo()
    sim.check("and can be undone too", meshes() == sorted([first, second, copy]) and active() == (first, 'SCULPT'),
              (meshes(), active()))
    before = sim.positions()
    centre = sim.project((0.0, 0.0, 0.0))
    yield from sim.drag(centre, (centre[0] - 50, centre[1] + 40))
    sim.check("and sculpting carries on after all of that", float(np.abs(sim.positions() - before).max()) > 1e-5)

    # ------------------------------------------------------------------ styles
    prefs.look_material_zbrush = 'GREY'
    yield 0.5
    prefs.look_style = 'KLAY'
    yield 0.8
    saved = json.loads(prefs.look_theme_backup or "{}")
    sim.check("the KlaySculpt style swaps the colour theme", theme.wearing() == 'KLAY', theme.wearing())
    sim.check("its canvas is night blue", gradients.gradient[2] > 1.5 * gradients.gradient[0],
              tuple(round(v, 3) for v in gradients.gradient))
    sim.check("its panels float over the canvas again", preferences.system.use_region_overlap)
    strays = [path for path in saved if not (close(saved[path], blender[path], 0.006)
                                             if isinstance(blender[path], (list, float)) else saved[path] == blender[path])]
    sim.check("the way back still leads to the theme from before either style", len(saved) > 200 and not strays,
              strays[:3])
    sim.check("and the sculpt is in brown clay", matcap() == "clay_brown.exr", matcap())
    sim.screenshot("look_4_klay_layout.png")
    prefs.look_style = 'ZBRUSH'
    yield 0.8
    sim.check("back to the ZBrush style, which remembers its own material", theme.wearing() == 'ZBRUSH'
              and matcap() == "sculptkit_grey.exr" and not preferences.system.use_region_overlap,
              (theme.wearing(), matcap()))
    prefs.look_material_zbrush = 'RED_WAX'
    yield 0.5

    # ------------------------------------------------- going back and forth
    with sim.override():
        bpy.ops.sculptkit.zbrush_layout()
    yield 1.0
    sim.check("pressing ZBrush Layout again goes back, still in Sculpt Mode",
              workspace() == came_from and bpy.context.mode == 'SCULPT', (workspace(), bpy.context.mode))
    with sim.override():
        bpy.ops.sculptkit.zbrush_layout()
    yield 1.0
    sim.check("and once more returns to the same workspace", workspace() == LAYOUT and kit_workspaces() == [LAYOUT]
              and len(sim.window.screen.areas) == 1, kit_workspaces())

    # ------------------------------------------------------ switching parts off
    screens = len(bpy.data.screens)
    prefs.look_layout = False
    yield 1.5
    sim.check("switching the layout off takes its workspace out of the file", not kit_workspaces()
              and workspace() == came_from and len(bpy.data.screens) == screens - 1
              and not hasattr(bpy.types, "SCULPTKIT_OT_zbrush_layout"), (kit_workspaces(), workspace()))
    sim.check("and no other workspace", sorted(w.name for w in bpy.data.workspaces) == workspaces_at_start,
              sorted(w.name for w in bpy.data.workspaces))
    prefs.look_layout = True
    yield 0.3

    prefs.look_shelf = False
    yield 0.3
    sim.check("switching the shelf off gives Blender's brush bar back", blender_bar()
              and not hasattr(bpy.types, "SCULPTKIT_PT_material") and theme.wearing() == 'ZBRUSH')
    prefs.look_shelf = True

    prefs.look_material = False
    yield 0.3
    sim.check("switching the material off returns viewports to Blender's plain one", matcap() == "basic_bright.exr",
              matcap())
    sim.check("and takes the kit's grey out of Blender's matcaps", not loaded("sculptkit_grey.exr"))
    prefs.look_material = True
    yield 0.3
    sim.check("and switching it on puts the style's material back", matcap() == "red_wax.exr"
              and loaded("sculptkit_grey.exr"), matcap())

    prefs.look_theme = False
    yield 0.5
    sim.check("switching the colour theme off restores Blender's theme", theme.wearing() is None
              and gradients.background_type == 'SINGLE_COLOR' and preferences.system.use_region_overlap
              and prefs.look_theme_backup == "", gradients.background_type)
    sim.screenshot("look_5_theme_off.png")
    prefs.look_theme = True
    yield 0.5
    sim.check("and on again dresses it", theme.wearing() == 'ZBRUSH')

    prefs.use_zbrush_look = False
    yield 0.8
    sim.check("the main switch takes all of it away", theme.wearing() is None
              and blender_bar() and matcap() == "basic_bright.exr"
              and not any(hasattr(bpy.types, name) for name in (
                  "SCULPTKIT_PT_subtools", "SCULPTKIT_OT_subtool_select", "SCULPTKIT_OT_zbrush_layout",
                  "SCULPTKIT_PT_material")), (theme.wearing(), matcap()))
    before = sim.positions()
    centre = sim.project(bpy.context.active_object.matrix_world.translation)
    yield from sim.drag(centre, (centre[0] + 40, centre[1] + 40))
    sim.check("and sculpting carries on without it", float(np.abs(sim.positions() - before).max()) > 1e-5)
    prefs.use_zbrush_look = True
    yield 0.8
    sim.check("and brings it back", theme.wearing() == 'ZBRUSH' and look.shelf.in_place()
              and matcap() == "red_wax.exr" and hasattr(bpy.types, "SCULPTKIT_PT_subtools"),
              (theme.wearing(), matcap()))


simlib.run("sim_zbrush_look", test)
