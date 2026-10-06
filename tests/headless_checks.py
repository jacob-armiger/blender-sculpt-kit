"""Checks that need no window: registration, the per-feature switches, the brush files.

Run by tests/run.sh inside a throwaway Blender profile with the extension
installed and enabled:

    BLENDER_USER_RESOURCES=<profile> blender -b --python tests/headless_checks.py
"""
import importlib
import json
import math
import os
import sys
import tomllib

import addon_utils
import bmesh
import bpy
import numpy as np
from mathutils import Matrix

MODULE = "bl_ext.user_default.sculpt_kit"
FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("  [%s]" % (detail,) if detail != "" else ""))
    if not ok:
        FAILED.append(name)


REMESH_KEY = ("sculptkit.voxel_remesh", 'R', True, False, False)


def all_kit_keys():
    keymap = bpy.context.window_manager.keyconfigs.addon.keymaps.get("Sculpt")
    return [] if keymap is None else [(i.idname, i.type, i.ctrl, i.shift, i.alt) for i in keymap.keymap_items]


def kit_keys():
    """The ZBrush keys: everything the kit binds except the remesh feature's own Ctrl + R."""
    return [key for key in all_kit_keys() if key != REMESH_KEY]


def has_key(idname, key, ctrl=False, shift=False, alt=False):
    return (idname, key, ctrl, shift, alt) in kit_keys()


def stroke_key(ctrl=False, shift=False, alt=False):
    """What the kit's brush-stroke binding for these modifiers does, or None."""
    keymap = bpy.context.window_manager.keyconfigs.addon.keymaps.get("Sculpt")
    for i in (keymap.keymap_items if keymap else ()):
        if (i.idname, i.type, i.ctrl, i.shift, i.alt) == ("sculpt.brush_stroke", 'LEFTMOUSE', ctrl, shift, alt):
            return {"brush_toggle": i.properties.brush_toggle, "mode": i.properties.mode}
    return None


def canvas_key(ctrl=False, shift=False, alt=False):
    """Which navigation a drag on empty canvas with these modifiers starts, or None."""
    keymap = bpy.context.window_manager.keyconfigs.addon.keymaps.get("Sculpt")
    for i in (keymap.keymap_items if keymap else ()):
        if (i.idname, i.type, i.ctrl, i.shift, i.alt) == ("sculptkit.canvas_navigate", 'LEFTMOUSE', ctrl, shift, alt):
            return i.properties.action
    return None


def library_paths():
    return [os.path.normpath(lib.path) for lib in bpy.context.preferences.filepaths.asset_libraries]


def registered(name):
    return hasattr(bpy.types, name)


addon = bpy.context.preferences.addons.get(MODULE)
check("extension is enabled", addon is not None)
if addon is None:
    sys.exit(1)
prefs = addon.preferences
kit = importlib.import_module(MODULE)
assets = os.path.join(os.path.dirname(kit.__file__), "assets")
kit_library = os.path.normpath(os.path.join(assets, "kit"))
kingslien_library = os.path.normpath(os.path.join(assets, "kingslien"))

# ---- defaults
switches = ["use_zbrush_keys", "use_zbrush_look", "use_kit_brushes", "use_kingslien_brushes", "use_reference_view",
            "use_sculpt_session", "use_remesh", "use_brush_tuning"]
check("every feature is on by default", all(getattr(prefs, s) for s in switches))
LOOK_CLASSES = ("SCULPTKIT_OT_zbrush_layout", "SCULPTKIT_OT_brush_direction", "SCULPTKIT_PT_material",
                "SCULPTKIT_OT_material",
                "SCULPTKIT_OT_subtool_select", "SCULPTKIT_OT_subtool_show", "SCULPTKIT_OT_subtool_duplicate",
                "SCULPTKIT_OT_subtool_merge_down", "SCULPTKIT_OT_subtool_delete", "SCULPTKIT_PT_subtools")
for cls in ("SCULPTKIT_OT_canvas_navigate", "SCULPTKIT_OT_canvas_mask", "SCULPTKIT_OT_divide",
            "SCULPTKIT_OT_reference_view", "SCULPTKIT_PT_reference_view", "SCULPTKIT_OT_new_sculpt",
            "SCULPTKIT_PT_sculpt_session", "SCULPTKIT_OT_copy_brush_tuning", "SCULPTKIT_PT_brush_tuning",
            "SCULPTKIT_OT_voxel_remesh", "SCULPTKIT_OT_quad_remesh", "SCULPTKIT_PT_remesh") + LOOK_CLASSES:
    check("registered " + cls, registered(cls))

# ---- ZBrush keys
check("right-drag rotates", has_key("view3d.rotate", 'RIGHTMOUSE'))
check("Alt + right-drag moves", has_key("view3d.move", 'RIGHTMOUSE', alt=True))
check("Ctrl + right-drag zooms", has_key("view3d.zoom", 'RIGHTMOUSE', ctrl=True))
check("canvas drag rotates", canvas_key() == 'ROTATE', canvas_key())
check("Alt + canvas drag moves", canvas_key(alt=True) == 'MOVE', canvas_key(alt=True))
check("Alt inverts", has_key("sculpt.brush_stroke", 'LEFTMOUSE', alt=True))
check("Ctrl masks", stroke_key(ctrl=True) == {"brush_toggle": 'MASK', "mode": 'NORMAL'}, stroke_key(ctrl=True))
check("Ctrl + Alt unmasks", stroke_key(ctrl=True, alt=True) == {"brush_toggle": 'MASK', "mode": 'INVERT'},
      stroke_key(ctrl=True, alt=True))
check("Alt inverts the brush", stroke_key(alt=True) == {"brush_toggle": 'None', "mode": 'INVERT'}, stroke_key(alt=True))
check("canvas gestures follow the mask stroke", has_key("sculptkit.canvas_mask", 'LEFTMOUSE', ctrl=True))
for key, idname in (('S', "wm.radial_control"), ('U', "wm.radial_control"), ('F', "view3d.view_all"),
                    ('X', "wm.context_toggle"), ('P', "view3d.view_persportho"), ('SPACE', "wm.call_panel"),
                    ('B', "wm.call_asset_shelf_popover"), ('D', "object.subdivision_set")):
    check("hotkey %s" % key, has_key(idname, key))
check("Ctrl + D divides", has_key("sculptkit.divide", 'D', ctrl=True))
all_keys = len(kit_keys())

prefs.keys_right_click = False
check("right-click navigation switches off alone",
      not has_key("view3d.rotate", 'RIGHTMOUSE') and canvas_key() == 'ROTATE' and has_key("wm.radial_control", 'S'))
prefs.keys_right_click = True
prefs.keys_canvas = False
check("canvas navigation switches off alone",
      canvas_key() is None and has_key("view3d.rotate", 'RIGHTMOUSE')
      and has_key("sculpt.brush_stroke", 'LEFTMOUSE', alt=True))
prefs.keys_canvas = True
prefs.keys_modifiers = False
check("ZBrush modifiers switch off alone",
      not has_key("sculptkit.canvas_mask", 'LEFTMOUSE', ctrl=True) and canvas_key() == 'ROTATE'
      and canvas_key(ctrl=True) == 'ZOOM' and stroke_key(ctrl=True) == {"brush_toggle": 'None', "mode": 'INVERT'})
prefs.keys_modifiers = True
prefs.keys_hotkeys = False
check("hotkeys switch off alone", not has_key("wm.radial_control", 'S') and has_key("view3d.rotate", 'RIGHTMOUSE'))
prefs.keys_hotkeys = True
check("all keys come back", len(kit_keys()) == all_keys, "%d of %d" % (len(kit_keys()), all_keys))

prefs.use_zbrush_keys = False
check("ZBrush keys off removes every key and operator", not kit_keys() and not registered("SCULPTKIT_OT_canvas_mask"))
check("the remesh shortcut stays", REMESH_KEY in all_kit_keys())
check("other features stay on", registered("SCULPTKIT_OT_reference_view") and kit_library in library_paths())
prefs.use_zbrush_keys = True
check("ZBrush keys back on", len(kit_keys()) == all_keys and registered("SCULPTKIT_OT_canvas_mask"))

# ---- ZBrush look: colour theme
look = importlib.import_module(MODULE + ".features.zbrush_look")
theme = look.theme
preferences = bpy.context.preferences
ui = preferences.themes[0].user_interface
gradients = preferences.themes[0].view_3d.space.gradients
themes = theme.data()
zbrush, klay, blender = themes["styles"]['ZBRUSH'], themes["styles"]['KLAY'], themes["blender"]
styled = set(zbrush) | set(klay)


def in_place(values, paths=None):
    """Whether Blender's preferences hold these values, at every path or at the ones given."""
    return all(theme._in_place(path, values[path]) for path in (values if paths is None else paths))


def saved():
    return json.loads(prefs.look_theme_backup) if prefs.look_theme_backup else {}


def rgb(colour):
    return tuple(round(v, 2) for v in colour[:3])


def same_value(a, b):
    if isinstance(b, list):
        return all(abs(x - y) < 0.006 for x, y in zip(a, b))
    return abs(a - b) < 0.006 if isinstance(b, float) else a == b


parts = ["look_theme", "look_layout", "look_shelf", "look_subtools", "look_material"]
check("every part of the look is on by default, in the ZBrush style",
      all(getattr(prefs, p) for p in parts) and prefs.look_style == 'ZBRUSH', prefs.look_style)
check("the two styles are told apart", len(zbrush) > 200 and len(klay) > 200
      and sum(zbrush[p] == klay[p] for p in zbrush if p in klay) < 10)
check("the ZBrush colour theme is in place", theme.wearing() == 'ZBRUSH' and in_place(zbrush), theme.wearing())
check("what it replaced is kept with the preferences", set(saved()) == set(zbrush)
      and saved()["system.use_region_overlap"] is True, len(saved()))
check("ZBrush colours: solid shelves, orange highlights, a canvas fading from black to grey",
      not preferences.system.use_region_overlap and gradients.background_type == 'LINEAR'
      and rgb(gradients.high_gradient) == (0.0, 0.0, 0.0) and rgb(ui.wcol_tool.inner_sel) == (0.94, 0.44, 0.0),
      (gradients.background_type, rgb(ui.wcol_tool.inner_sel)))

prefs.look_style = 'KLAY'
check("the KlaySculpt style replaces it", theme.wearing() == 'KLAY' and in_place(klay), theme.wearing())
check("and undoes what only the ZBrush style changes", in_place(blender, set(zbrush) - set(klay))
      and preferences.system.use_region_overlap)
check("KlaySculpt colours: coral highlights on slate, a night-blue canvas",
      rgb(ui.wcol_option.inner_sel) == (0.89, 0.65, 0.61) and gradients.gradient[2] > 1.5 * gradients.gradient[0],
      (rgb(ui.wcol_option.inner_sel), rgb(gradients.gradient)))
check("its way back leads to the theme from before either style", set(saved()) == set(klay)
      and all(same_value(value, blender[path]) for path, value in saved().items()), len(saved()))
prefs.look_style = 'ZBRUSH'
check("and back to the ZBrush style", theme.wearing() == 'ZBRUSH' and in_place(zbrush))

prefs.look_theme = False
check("switching the colour theme off restores every value", theme.wearing() is None and in_place(blender, styled)
      and prefs.look_theme_backup == "", theme.wearing())
ui.panel_back = (0.1, 0.2, 0.3, 1.0)
prefs.look_theme = True
check("and on again dresses it", theme.wearing() == 'ZBRUSH' and in_place(zbrush))
prefs.look_theme = False
check("a colour of your own comes back as it was, not as Blender's default", rgb(ui.panel_back) == (0.1, 0.2, 0.3),
      rgb(ui.panel_back))
ui.panel_back = blender["themes[0].user_interface.panel_back"]

prefs.look_theme = True
ui.wcol_tool.inner = (0.5, 0.1, 0.1, 1.0)
ui.wcol_regular.text = (0.2, 0.9, 0.2)
ui.panel_header = (0.1, 0.1, 0.3, 1.0)
prefs.look_theme = False
check("a few colours changed by hand while it was on do not stop it being put back", in_place(blender, styled))
prefs.look_theme = True
theme._write({path: blender[path] for path in zbrush})  # as if another theme had been picked in Preferences
prefs.look_theme = False
check("a theme picked by hand since is left as it is", in_place(blender, styled) and prefs.look_theme_backup == "")
prefs.look_theme = True
prefs.look_theme_backup = ""
prefs.look_theme = False
check("with nothing saved to go back to, it falls back to Blender's own theme", in_place(blender, styled))
prefs.look_theme = True
check("the colour theme is back on", theme.wearing() == 'ZBRUSH' and set(saved()) == set(zbrush))

# ---- ZBrush look: the other parts
tool_bar = bpy.types.VIEW3D_HT_tool_header


def bar_modules():
    return [f.__module__.replace(MODULE, "kit") for f in tool_bar._dyn_ui_initialize()]


def header_has(cls, function_name):
    return any(f.__name__ == function_name for f in cls._dyn_ui_initialize())


check("the brush bar is the ZBrush shelf", bar_modules() == ["kit.features.zbrush_look.shelf"], bar_modules())
check("the viewport header has the view switches", header_has(bpy.types.VIEW3D_HT_header, "_view_switches"))
check("the top bar has the ZBrush Layout button", header_has(bpy.types.TOPBAR_HT_upper_bar, "_top_bar_button"))
prefs.look_shelf = False
check("the shelf switches off alone", bar_modules() == ["bl_ui.space_view3d"]
      and not header_has(bpy.types.VIEW3D_HT_header, "_view_switches") and not registered("SCULPTKIT_PT_material")
      and registered("SCULPTKIT_PT_subtools") and theme.wearing() == 'ZBRUSH', bar_modules())
prefs.look_shelf = True


def another_addon(self, context):
    pass


tool_bar.append(another_addon)
check("another add-on can add to the bar beside it", bar_modules() == ["kit.features.zbrush_look.shelf", "__main__"],
      bar_modules())
prefs.look_shelf = False
check("and it steps out without disturbing that", bar_modules() == ["bl_ui.space_view3d", "__main__"], bar_modules())
prefs.look_shelf = True
tool_bar.remove(another_addon)
check("or be taken off again while it is on", bar_modules() == ["kit.features.zbrush_look.shelf"], bar_modules())

prefs.look_subtools = False
check("the subtool list switches off alone", not registered("SCULPTKIT_PT_subtools")
      and not registered("SCULPTKIT_OT_subtool_select") and registered("SCULPTKIT_OT_zbrush_layout"))
prefs.look_subtools = True
prefs.look_layout = False
check("the layout switches off alone", not registered("SCULPTKIT_OT_zbrush_layout")
      and not header_has(bpy.types.TOPBAR_HT_upper_bar, "_top_bar_button") and registered("SCULPTKIT_PT_subtools"))
prefs.look_layout = True

material = look.material
GREY = "sculptkit_grey.exr"


def matcaps():
    return [light.name for light in preferences.studio_lights if light.type == 'MATCAP']


check("the kit's grey matcap is bundled", os.path.isfile(os.path.join(assets, "matcaps", GREY)))
check("ZBrush starts on red wax and KlaySculpt on brown clay",
      prefs.look_material_zbrush == 'RED_WAX' and prefs.look_material_klay == 'BROWN_CLAY',
      (prefs.look_material_zbrush, prefs.look_material_klay))
views = [space for screen in bpy.data.screens for area in screen.areas for space in area.spaces if space.type == 'VIEW_3D']
if views and "red_wax.exr" in matcaps() and "clay_brown.exr" in matcaps():
    check("the kit's grey is in Blender's list of matcaps, once", matcaps().count(GREY) == 1, matcaps().count(GREY))
    shading, other = views[0].shading, views[-1].shading
    shading.type, shading.light = 'SOLID', 'MATCAP'
    shading.studio_light = "basic_bright.exr"
    material.apply(shading)
    check("the material is red wax in the ZBrush style", shading.studio_light == "red_wax.exr", shading.studio_light)
    prefs.look_material_zbrush = 'GREY'
    check("picking Grey moves a viewport that shows a kit material over to it", shading.studio_light == GREY,
          shading.studio_light)
    if other != shading:
        other.type, other.light = 'SOLID', 'MATCAP'
        other.studio_light = "resin.exr"
        prefs.look_material_zbrush = 'RED_WAX'
        prefs.look_material_zbrush = 'GREY'
        check("and leaves a viewport on a matcap of your own choosing alone", other.studio_light == "resin.exr"
              and shading.studio_light == GREY, other.studio_light)
        other.studio_light = "basic_bright.exr"
    prefs.look_style = 'KLAY'
    check("the KlaySculpt style brings its brown clay", shading.studio_light == "clay_brown.exr", shading.studio_light)
    prefs.look_material_klay = 'GREY'
    check("and can be put on grey as well", shading.studio_light == GREY, shading.studio_light)
    prefs.look_material_klay = 'BROWN_CLAY'
    prefs.look_style = 'ZBRUSH'
    check("each style remembers its own pick", shading.studio_light == GREY and prefs.look_material_klay == 'BROWN_CLAY',
          shading.studio_light)
    prefs.look_material = False
    check("the material switches off alone, back to Blender's plain one",
          shading.studio_light == "basic_bright.exr" and theme.wearing() == 'ZBRUSH', shading.studio_light)
    check("taking the kit's grey out of the list", GREY not in matcaps())
    material.apply(shading)
    check("and then leaves viewports alone", shading.studio_light == "basic_bright.exr")
    prefs.look_material = True
    check("switching it on dresses the viewports that show a matcap", shading.studio_light == GREY
          and matcaps().count(GREY) == 1, shading.studio_light)
    prefs.look_material_zbrush = 'RED_WAX'
    check("and red wax is one pick away", shading.studio_light == "red_wax.exr", shading.studio_light)
else:
    print("SKIP  material checks: no 3D viewport or matcaps in this session")

prefs.use_zbrush_look = False
check("the main switch takes the whole look away", theme.wearing() is None and in_place(blender, styled)
      and bar_modules() == ["bl_ui.space_view3d"] and not any(registered(cls) for cls in LOOK_CLASSES)
      and prefs.look_theme_backup == "", (theme.wearing(), bar_modules()))
check("and nothing else", len(kit_keys()) == all_keys and registered("SCULPTKIT_OT_new_sculpt"))
prefs.look_theme = False
prefs.use_zbrush_look = True
check("back on, it leaves out a part that is switched off", theme.wearing() is None and in_place(blender, styled)
      and bar_modules() == ["kit.features.zbrush_look.shelf"] and registered("SCULPTKIT_PT_subtools"))
prefs.look_theme = True
check("the whole look is back", theme.wearing() == 'ZBRUSH' and all(registered(cls) for cls in LOOK_CLASSES))

# ---- brush libraries
check("Move brush library listed", kit_library in library_paths())
check("Kingslien library listed", kingslien_library in library_paths())
prefs.use_kit_brushes = False
check("Move brush library switches off alone", kit_library not in library_paths() and kingslien_library in library_paths())
prefs.use_kit_brushes = True
prefs.use_kingslien_brushes = False
check("Kingslien library switches off alone", kingslien_library not in library_paths() and kit_library in library_paths())
prefs.use_kingslien_brushes = True
check("both libraries back", kit_library in library_paths() and kingslien_library in library_paths())

with bpy.data.libraries.load(os.path.join(kit_library, "SculptKit_Brushes.blend"), assets_only=True) as (src, dst):
    dst.brushes = list(src.brushes)
move = next((b for b in dst.brushes if b.name == "SculptKit_Move"), None)
check("Move brush is an asset in the kit library", move is not None)
if move is not None:
    check("Move brush grabs through the model", move.sculpt_brush_type == 'GRAB' and move.falloff_shape == 'PROJECTED',
          "%s %s" % (move.sculpt_brush_type, move.falloff_shape))


def teeth_of(brush):
    """How many separate teeth the brush's alpha has across the stroke."""
    image = brush.texture.image
    width, height = image.size
    row = np.array(image.pixels[:]).reshape(height, width, -1)[height // 2, :, 0]
    return int(((row[1:] > 0.15) & (row[:-1] <= 0.15)).sum())


rakes = {b.name: b for b in dst.brushes if "Rake" in b.name}
check("two rakes in the kit library", sorted(rakes) == ["SculptKit_Rake", "SculptKit_Rake_Fine"], sorted(rakes))
for name, teeth in (("SculptKit_Rake", 5), ("SculptKit_Rake_Fine", 10)):
    rake = rakes.get(name)
    if rake is None:
        continue
    check("%s is a clay rake with %d teeth that follow the stroke" % (name, teeth),
          rake.sculpt_brush_type == 'CLAY_STRIPS' and rake.texture_slot.use_rake and teeth_of(rake) == teeth,
          (rake.sculpt_brush_type, teeth_of(rake)))
    lightest = tuple(round(v, 2) for v in rake.curve_strength.curves[0].points[0].location)
    check("%s: pen pressure sets how deep it bites, not how wide it is" % name,
          not rake.use_pressure_size and rake.use_pressure_strength and lightest == (0.0, 0.2), lightest)
with bpy.data.libraries.load(os.path.join(kingslien_library, "Kingslien_Brushes.blend"), assets_only=True) as (src, dst):
    dst.brushes = list(src.brushes)
names = sorted(b.name for b in dst.brushes)
check("six Kingslien brushes bundled", len(names) == 6, names)
clay = next(b for b in dst.brushes if b.name == "Kingslien_Clay")
size_curve = [tuple(round(v, 3) for v in p.location) for p in clay.curve_size.curves[0].points]
check("Clay keeps a quarter of its size at the lightest pressure", clay.use_pressure_size and size_curve[0] == (0.0, 0.25),
      size_curve)

# ---- the other switches
prefs.use_reference_view = False
check("reference view switches off alone",
      not registered("SCULPTKIT_OT_reference_view") and not hasattr(bpy.types.Scene, "sculpt_kit_reference")
      and registered("SCULPTKIT_OT_new_sculpt"))
prefs.use_reference_view = True
check("reference view back on", registered("SCULPTKIT_OT_reference_view") and hasattr(bpy.types.Scene, "sculpt_kit_reference"))
prefs.use_sculpt_session = False
check("new sculpt switches off alone", not registered("SCULPTKIT_OT_new_sculpt") and registered("SCULPTKIT_OT_copy_brush_tuning"))
prefs.use_sculpt_session = True
prefs.use_brush_tuning = False
check("brush tuning switches off alone", not registered("SCULPTKIT_OT_copy_brush_tuning") and registered("SCULPTKIT_OT_new_sculpt"))
prefs.use_brush_tuning = True
check("Ctrl + R runs the kit's voxel remesh", REMESH_KEY in all_kit_keys())
prefs.use_remesh = False
check("remesh tools switch off alone",
      not registered("SCULPTKIT_OT_voxel_remesh") and REMESH_KEY not in all_kit_keys()
      and not hasattr(bpy.types.Scene, "sculpt_kit_remesh_resolution") and len(kit_keys()) == all_keys)
prefs.use_remesh = True
check("remesh tools back on", registered("SCULPTKIT_OT_quad_remesh") and REMESH_KEY in all_kit_keys())

# ---- new sculpt sphere
bpy.ops.sculptkit.new_sculpt(density='6')
ob = bpy.context.active_object
radii = [v.co.length for v in ob.data.vertices]
check("new sculpt makes a 25k quad sphere", ob.name.startswith("Sculpt") and len(ob.data.polygons) == 6 * 4 ** 6
      and all(len(p.vertices) == 4 for p in ob.data.polygons), len(ob.data.polygons))
check("sphere radius is 1", abs(min(radii) - 1.0) < 1e-4 and abs(max(radii) - 1.0) < 1e-4, (min(radii), max(radii)))
check("new sculpt enters Sculpt Mode", bpy.context.mode == 'SCULPT', bpy.context.mode)

# ---- divide
bpy.ops.sculptkit.divide()
multires = next((m for m in ob.modifiers if m.type == 'MULTIRES'), None)
check("divide adds a Multires level", multires is not None and multires.total_levels == 1,
      None if multires is None else multires.total_levels)
bpy.ops.sculptkit.divide()
check("divide again adds a second level", multires.total_levels == 2, multires.total_levels)

# ---- brush tuning round trip
brush_settings = importlib.import_module(MODULE + ".brush_settings")
brush = bpy.context.tool_settings.sculpt.brush
check("a brush is active in Sculpt Mode", brush is not None)
if brush is not None:
    text = brush_settings.to_toml(brush.name, brush_settings.read(brush))
    parsed = tomllib.loads(text)["brushes"][brush.name]
    check("copied tuning is valid TOML", parsed["strength"] == round(brush.strength, 4), parsed.get("strength"))
    target = brush.copy()
    parsed["strength"] = 0.123
    parsed["falloff"] = [[0.0, 1.0], [0.5, 0.8], [1.0, 0.0]]
    parsed["pressure_size_curve"] = [[0.0, 0.4], [1.0, 1.0]]
    parsed["bogus_key"] = 1
    rejected = brush_settings.apply(target, parsed)
    check("tuning applies to a brush", abs(target.strength - 0.123) < 1e-6
          and target.curve_distance_falloff_preset == 'CUSTOM'
          and len(target.curve_distance_falloff.curves[0].points) == 3
          and abs(target.curve_size.curves[0].points[0].location[1] - 0.4) < 1e-6,
          (target.strength, target.curve_distance_falloff_preset))
    check("unknown tuning keys are reported, not applied", rejected == ["bogus_key"], rejected)

# ---- remesh tools
bpy.ops.object.mode_set(mode='OBJECT')


def make_active(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    new = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(new)
    for other in bpy.context.scene.objects:
        other.select_set(False)
    new.select_set(True)
    bpy.context.view_layer.objects.active = new
    return new


def sphere(radius, subdivisions=5):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius)
    return make_active("Sphere", bm)


def size(ob_):
    co = np.array([v.co[:] for v in ob_.data.vertices])
    return np.ptp(co, axis=0)


def quad_share(ob_):
    return sum(len(p.vertices) == 4 for p in ob_.data.polygons) / max(1, len(ob_.data.polygons))


counts = []
for radius in (0.05, 1.0, 20.0):
    target = sphere(radius)
    bpy.ops.sculptkit.voxel_remesh(resolution=64)
    counts.append(len(target.data.polygons))
check("Voxel Remesh gives the same density at any model size", max(counts) < 1.1 * min(counts), counts)

target = sphere(1.0)
mask = target.data.attributes.new(".sculpt_mask", 'FLOAT', 'POINT')
mask.data.foreach_set("value", [1.0 if v.co.x > 0.0 else 0.0 for v in target.data.vertices])
bpy.ops.sculptkit.voxel_remesh(resolution=64)
mask = target.data.attributes.get(".sculpt_mask")
values = np.zeros(len(target.data.vertices))
if mask is not None:
    mask.data.foreach_get("value", values)
check("Voxel Remesh keeps the mask", mask is not None and 0.35 < (values > 0.5).mean() < 0.65,
      "masked share %.2f" % (values > 0.5).mean())


def fine_voxel_sheet():
    """A thin wavy sheet, voxel remeshed finely: the voxel remesher leaves a few near-zero edges on it."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((1.0, 1.0, 0.02, 1.0)))
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=6, use_grid_fill=True)
    for v in bm.verts:
        v.co.z += 0.02 * math.sin(v.co.x * 9) * math.cos(v.co.y * 7)
    sheet = make_active("Sheet", bm)
    sheet.data.remesh_voxel_size = 0.01
    bpy.ops.object.voxel_remesh()
    return sheet


target = fine_voxel_sheet()
check("Blender's own quad remesh refuses a finely voxel-remeshed sheet",
      'CANCELLED' in bpy.ops.object.quadriflow_remesh(mode='FACES', target_faces=4000, use_mesh_symmetry=False))
before = size(target)
result = bpy.ops.sculptkit.quad_remesh(target_faces=4000, use_symmetry=False)
check("Quad Remesh handles the same sheet", 'FINISHED' in result and 2000 < len(target.data.polygons) < 6500
      and quad_share(target) > 0.99, "%d faces, %.0f%% quads" % (len(target.data.polygons), 100 * quad_share(target)))
# (a coarser mesh of a wavy sheet is a little thicker; a scale mistake would be off by far more)
check("and leaves its size alone", np.allclose(size(target), before, rtol=0.02, atol=0.02), (before, size(target)))

target = sphere(0.004)
target.data.remesh_voxel_size = 0.008 / 128
bpy.ops.object.voxel_remesh()
check("Blender's own quad remesh refuses a small, dense model",
      'CANCELLED' in bpy.ops.object.quadriflow_remesh(mode='FACES', target_faces=4000, use_mesh_symmetry=False))
before = size(target)
result = bpy.ops.sculptkit.quad_remesh(target_faces=4000, use_symmetry=False)
check("Quad Remesh handles the small, dense model", 'FINISHED' in result and quad_share(target) > 0.99
      and np.allclose(size(target), before, rtol=0.05), "%d faces, size %s" % (len(target.data.polygons), size(target)))

# Real damage: an edge with three faces, a stray wire edge and a flipped face.
bm = bmesh.new()
bmesh.ops.create_icosphere(bm, subdivisions=4, radius=1.0)
bm.edges.ensure_lookup_table()
bm.faces.ensure_lookup_table()
edge = bm.edges[0]
fin_tip = bm.verts.new((edge.verts[0].co + edge.verts[1].co) * 0.6)
bm.faces.new((edge.verts[0], edge.verts[1], fin_tip))
bm.edges.new((bm.verts.new((3.0, 0.0, 0.0)), bm.verts.new((3.1, 0.0, 0.0))))
bm.faces.ensure_lookup_table()
bm.faces[40].normal_flip()
target = make_active("Damaged", bm)
result = bpy.ops.sculptkit.quad_remesh(target_faces=3000, use_symmetry=False)
check("Quad Remesh repairs real non-manifold damage first", 'FINISHED' in result and quad_share(target) > 0.99
      and np.allclose(size(target), (2.0, 2.0, 2.0), rtol=0.05), "%d faces, size %s" % (len(target.data.polygons), size(target)))

target = sphere(1.0)
bpy.ops.object.mode_set(mode='SCULPT')
result = bpy.ops.sculptkit.quad_remesh(target_faces=2000, use_symmetry=False)
check("Quad Remesh works from Sculpt Mode and returns to it", 'FINISHED' in result and bpy.context.mode == 'SCULPT'
      and quad_share(target) > 0.99, bpy.context.mode)

# ---- switching the whole extension off and on
bpy.ops.object.mode_set(mode='OBJECT')
addon_utils.disable(MODULE)
check("disabling the extension removes everything",
      not all_kit_keys() and kit_library not in library_paths() and kingslien_library not in library_paths()
      and not registered("SCULPTKIT_OT_new_sculpt") and not registered("SCULPTKIT_OT_quad_remesh")
      and not any(registered(cls) for cls in LOOK_CLASSES))
check("and puts Blender's theme, brush bar and matcaps back", theme.wearing() is None and in_place(blender, styled)
      and bar_modules() == ["bl_ui.space_view3d"] and "sculptkit_grey.exr" not in
      [light.name for light in preferences.studio_lights], (theme.wearing(), bar_modules()))
addon_utils.enable(MODULE)
check("enabling it again restores everything",
      len(kit_keys()) == all_keys and REMESH_KEY in all_kit_keys() and kit_library in library_paths()
      and registered("SCULPTKIT_OT_new_sculpt") and theme.wearing() == 'ZBRUSH'
      and bar_modules() == ["kit.features.zbrush_look.shelf"], (theme.wearing(), bar_modules()))

# ---- Blender closing down, which switches every add-on off at once after the preferences are saved
def backup():
    """The saved way back, read from the stored preferences (the class behind them comes and goes)."""
    return bpy.context.preferences.addons[MODULE].preferences.look_theme_backup


kept = backup()
addon_utils.disable_all()
check("on the way out the theme is left as it was saved", theme.wearing() == 'ZBRUSH' and in_place(zbrush),
      theme.wearing())
check("while the add-on itself is unloaded", not all_kit_keys() and not any(registered(cls) for cls in LOOK_CLASSES)
      and bar_modules() == ["bl_ui.space_view3d"], bar_modules())
addon_utils.enable(MODULE)
check("and the next start finds it in place, with its way back", theme.wearing() == 'ZBRUSH' and backup() == kept
      and len(kept) > 1000 and bar_modules() == ["kit.features.zbrush_look.shelf"], (theme.wearing(), bar_modules()))
check("with the kit's grey matcap loaded once, not twice",
      [light.name for light in preferences.studio_lights].count("sculptkit_grey.exr") == 1)

print("\n%d failed" % len(FAILED) if FAILED else "\nall headless checks passed")
sys.exit(1 if FAILED else 0)
