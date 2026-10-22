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
    """The ZBrush keys: everything the kit binds except the remesh feature's own Ctrl + R and the ZBrush look's
    watcher of the modifier keys (it binds nothing a person presses; it lights Zsub while Alt is held)."""
    return [key for key in all_kit_keys() if key != REMESH_KEY and key[0] != "sculptkit.watch_modifiers"]


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

# ---- defaults
switches = ["use_zbrush_look", "use_kit_brushes", "use_reference_view",
            "use_sculpt_session", "use_remesh", "use_subdivide"]
check("every feature is on by default, but for brush tuning, which is for development",
      all(getattr(prefs, s) for s in switches) and not prefs.use_brush_tuning)
check("the ZBrush keys and the colour theme wait for the welcome's answer", not prefs.use_zbrush_keys
      and not prefs.look_theme and not prefs.welcome_done)
bpy.ops.sculptkit.welcome('EXEC_DEFAULT', theme='KLAY', keys=True)
check("answering the welcome turns on what was picked", prefs.use_zbrush_keys and prefs.look_theme
      and prefs.look_style == 'KLAY' and prefs.welcome_done)
prefs.use_brush_tuning = True  # the checks below expect every feature on
LOOK_CLASSES = ("SCULPTKIT_OT_zbrush_layout", "SCULPTKIT_OT_brush_direction", "SCULPTKIT_PT_material",
                "SCULPTKIT_OT_material",
                "SCULPTKIT_OT_subtool_select", "SCULPTKIT_OT_subtool_show", "SCULPTKIT_OT_subtool_duplicate",
                "SCULPTKIT_OT_subtool_merge_down", "SCULPTKIT_OT_subtool_move", "SCULPTKIT_OT_subtool_delete",
                "SCULPTKIT_PT_subtools")
for cls in ("SCULPTKIT_OT_canvas_navigate", "SCULPTKIT_OT_canvas_mask", "SCULPTKIT_OT_divide",
            "SCULPTKIT_OT_reference_view", "SCULPTKIT_PT_reference_view", "SCULPTKIT_OT_new_sculpt",
            "SCULPTKIT_PT_sculpt_session", "SCULPTKIT_OT_copy_brush_tuning", "SCULPTKIT_OT_reset_brush", "SCULPTKIT_PT_brush_tuning",
            "SCULPTKIT_OT_voxel_remesh", "SCULPTKIT_OT_quad_remesh", "SCULPTKIT_PT_remesh",
            "SCULPTKIT_OT_multires_finish", "SCULPTKIT_OT_subdivide_mesh", "SCULPTKIT_PT_subdivide") + LOOK_CLASSES:
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
check("every part of the look is on, in the KlaySculpt style",
      all(getattr(prefs, p) for p in parts) and prefs.look_style == 'KLAY' and theme.wearing() == 'KLAY', prefs.look_style)
prefs.look_style = 'ZBRUSH'  # the checks below start from the ZBrush style
check("the two styles are told apart", len(zbrush) > 200 and len(klay) > 200
      and sum(zbrush[p] == klay[p] for p in zbrush if p in klay) < 10)
check("the ZBrush colour theme is in place", theme.wearing() == 'ZBRUSH' and in_place(zbrush), theme.wearing())
check("what it replaced is kept with the preferences", set(saved()) == set(zbrush)
      and saved()["system.use_region_overlap"] is True, len(saved()))
check("ZBrush colours: shelves floating over the canvas, orange highlights, a canvas fading from black to grey",
      preferences.system.use_region_overlap and gradients.background_type == 'LINEAR'
      and rgb(gradients.high_gradient) == (0.0, 0.0, 0.0) and rgb(ui.wcol_tool.inner_sel) == (0.94, 0.44, 0.0),
      (gradients.background_type, rgb(ui.wcol_tool.inner_sel)))

theme_module = importlib.import_module(MODULE + ".features.zbrush_look.theme")
preferences.system.use_region_overlap = False
prefs.look_theme_applied = "an older kit"
theme_module.enable()
check("a newer kit's changes to the style go on over the one already worn", preferences.system.use_region_overlap
      and theme_module.wearing() == 'ZBRUSH')
preferences.system.use_region_overlap = False
theme_module.enable()
check("but with the same kit, what was changed by hand since stays", not preferences.system.use_region_overlap)
preferences.system.use_region_overlap = True

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

# ---- brush library
check("the brush library is listed", kit_library in library_paths())
prefs.use_kit_brushes = False
check("and switches off", kit_library not in library_paths())
bpy.context.preferences.filepaths.asset_libraries.new(name="Sculpt Kit Kingslien", directory=assets)
prefs.use_kit_brushes = True
check("and back on, taking away the separate library of versions before 0.5", kit_library in library_paths()
      and "Sculpt Kit Kingslien" not in [lib.name for lib in bpy.context.preferences.filepaths.asset_libraries])

with bpy.data.libraries.load(os.path.join(kit_library, "SculptKit_Brushes.blend"), assets_only=True) as (src, dst):
    dst.brushes = list(src.brushes)
dots = next((b for b in dst.brushes if b.name == "SculptKit_Rake_Dots"), None)
check("the five-dot rake is in the kit library, on Blender's Clay brush, with its alpha following the stroke",
      dots is not None and dots.sculpt_brush_type == 'CLAY' and dots.texture is not None and dots.texture_slot.use_rake
      and dots.texture.image.size[:] == (512, 512), dots and dots.sculpt_brush_type)
if dots is not None:
    got = (dots.strength, dots.spacing, round(dots.hardness, 3), round(dots.auto_smooth_factor, 3),
           round(dots.normal_radius_factor, 3), round(dots.plane_offset, 3), dots.use_accumulate, dots.use_pressure_strength,
           dots.use_pressure_size, dots.use_inverse_smooth_pressure, dots.use_space_attenuation)
    check("with the settings it was shared with", got == (0.5, 3, 0.65, 0.25, 0.75, 0.4, True, True, False, True, True), got)
clay_rake = next((b for b in dst.brushes if b.name == "SculptKit_Clay_Rake_V3"), None)
check("the clay rake is in the kit library, on Clay Strips, with its raked strip following the stroke",
      clay_rake is not None and clay_rake.sculpt_brush_type == 'CLAY_STRIPS' and clay_rake.texture is not None
      and clay_rake.texture_slot.use_rake and clay_rake.use_pressure_size and clay_rake.use_pressure_strength,
      clay_rake and clay_rake.sculpt_brush_type)
check("the earlier clay rakes are gone",
      not any(b.name in ("SculptKit_Clay_Rake", "SculptKit_Clay_Rake_V2") for b in dst.brushes))
kit_types = {b.name: b.sculpt_brush_type for b in dst.brushes}
check("the kit has copies of Blender's Clay Strips and Clay (as Clay Soft)",
      kit_types.get("SculptKit_Clay_Strips_Copy") == 'CLAY_STRIPS' and kit_types.get("SculptKit_Clay_Soft") == 'CLAY',
      kit_types)
check("the add-on ships the Sculpt Kit brushes and nothing else",
      sorted(kit_types) == sorted([
          "SculptKit_Clay_V1", "SculptKit_Clay_V2", "SculptKit_Clay_Rake_V3", "SculptKit_Clay_Soft",
          "SculptKit_Clay_Strips_Copy", "SculptKit_Move", "SculptKit_Rake_Dots", "SculptKit_Rake_V2"]),
      sorted(kit_types))
descriptions = {b.name: b.asset_data.description for b in dst.brushes}
check("every description says what the brush is good for, without naming other programs, people or keys",
      all("Good for" in text for text in descriptions.values())
      and not any(word in text for text in descriptions.values()
                  for word in ("ZBrush", "Kingslien", "Discord", "Ctrl", "Alt ")), descriptions)
sk_clay = next((b for b in dst.brushes if b.name == "SculptKit_Clay_V2"), None)
size_curve = sk_clay and [tuple(round(v, 3) for v in p.location) for p in sk_clay.curve_size.curves[0].points]
check("SculptKit_Clay_V2 keeps a quarter of its size at the lightest pressure",
      sk_clay is not None and sk_clay.use_pressure_size and size_curve[0] == (0.0, 0.25), size_curve)
check("SculptKit_Clay_V2 has Jacob's tuning",
      sk_clay is not None and sk_clay.sculpt_brush_type == 'CLAY_STRIPS' and round(sk_clay.strength, 4) == 0.54
      and round(sk_clay.tip_roundness, 3) == 0.7, sk_clay and (sk_clay.strength, sk_clay.tip_roundness))
move = next((b for b in dst.brushes if b.name == "SculptKit_Move"), None)
check("Move brush is an asset in the kit library", move is not None)
if move is not None:
    check("Move brush grabs through the model", move.sculpt_brush_type == 'GRAB' and move.falloff_shape == 'PROJECTED',
          "%s %s" % (move.sculpt_brush_type, move.falloff_shape))


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

prefs.use_subdivide = False
check("subdivide switches off alone, Divide staying for Ctrl + D",
      not registered("SCULPTKIT_PT_subdivide") and not registered("SCULPTKIT_OT_subdivide_mesh")
      and registered("SCULPTKIT_OT_divide") and has_key("sculptkit.divide", 'D', ctrl=True))
prefs.use_zbrush_keys = False
check("with the ZBrush keys off too, Divide goes", not registered("SCULPTKIT_OT_divide"))
prefs.use_subdivide = True
check("subdivide alone brings Divide back", registered("SCULPTKIT_OT_divide") and registered("SCULPTKIT_PT_subdivide"))
prefs.use_zbrush_keys = True
check("both back on", registered("SCULPTKIT_OT_divide") and has_key("sculptkit.divide", 'D', ctrl=True))

# subdivide at work, on a cube (6 faces)
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.active_object
bpy.ops.sculptkit.subdivide_mesh('EXEC_DEFAULT', True)
check("Subdivide Mesh smooths the mesh into four times the faces, leaving no modifier",
      len(cube.data.polygons) == 24 and not cube.modifiers, (len(cube.data.polygons), list(cube.modifiers)))
bpy.ops.object.mode_set(mode='SCULPT')
bpy.ops.sculptkit.divide('EXEC_DEFAULT', True)
bpy.ops.sculptkit.divide('EXEC_DEFAULT', True)
multires = cube.modifiers[0] if cube.modifiers else None
check("Divide twice gives a Multires with two levels, sculpting on the top one",
      multires is not None and multires.type == 'MULTIRES' and multires.total_levels == 2 and multires.sculpt_levels == 2,
      multires and (multires.type, multires.total_levels, multires.sculpt_levels))
check("Subdivide Mesh steps aside while there are levels", not bpy.ops.sculptkit.subdivide_mesh.poll())
check("and both remeshes refuse, as Blender's remeshers would",
      not bpy.ops.sculptkit.voxel_remesh.poll() and not bpy.ops.sculptkit.quad_remesh.poll())
multires.sculpt_levels = 1
bpy.ops.sculptkit.multires_finish('EXEC_DEFAULT', True, action='APPLY')
check("Apply bakes the level shown into the mesh and stays in Sculpt Mode",
      not cube.modifiers and len(cube.data.polygons) == 96 and cube.mode == 'SCULPT',
      (len(cube.data.polygons), list(cube.modifiers), cube.mode))
bpy.ops.sculptkit.divide('EXEC_DEFAULT', True)
bpy.ops.sculptkit.multires_finish('EXEC_DEFAULT', True, action='REMOVE')
check("Remove drops the levels, keeping the base mesh", not cube.modifiers and len(cube.data.polygons) == 96)
bpy.ops.object.mode_set(mode='OBJECT')
bpy.data.objects.remove(cube)

# ---- new sculpt sphere
on_screen = [o for o in bpy.context.view_layer.objects if o.type == 'MESH' and o.visible_get()]
bpy.ops.sculptkit.new_sculpt()
ob = bpy.context.active_object
check("Blender's starting cube is hidden to make room for the first sphere", [o.name for o in on_screen] == ["Cube"]
      and not on_screen[0].visible_get() and tuple(ob.location) == (0.0, 0.0, 0.0), [o.name for o in on_screen])
radii = [v.co.length for v in ob.data.vertices]
check("new sculpt makes a 25k quad sphere", ob.name.startswith("Sphere") and len(ob.data.polygons) == 6 * 4 ** 6
      and all(len(p.vertices) == 4 for p in ob.data.polygons), len(ob.data.polygons))
check("it is the size of that cube: radius 1", abs(min(radii) - 1.0) < 1e-4 and abs(max(radii) - 1.0) < 1e-4,
      (min(radii), max(radii)))
check("new sculpt enters Sculpt Mode", bpy.context.mode == 'SCULPT', bpy.context.mode)
bpy.ops.sculptkit.new_sculpt()
part = bpy.context.active_object
check("one added to a sculpt is the same size", part != ob and bpy.context.mode == 'SCULPT' and ob.visible_get()
      and all(abs(d - 2.0) < 1e-3 for d in part.dimensions), tuple(part.dimensions))
bpy.ops.sculptkit.new_sculpt(shape='CUBE', location=(6.0, 0.0, 0.0))
cube = bpy.context.active_object
corners = np.array([v.co for v in cube.data.vertices])
edges = np.array([(cube.data.vertices[e.vertices[0]].co - cube.data.vertices[e.vertices[1]].co).length for e in cube.data.edges])
check("the cube is 25k quads, 2 units across, in Sculpt Mode", len(cube.data.polygons) == 6 * 4 ** 6
      and all(len(p.vertices) == 4 for p in cube.data.polygons) and all(abs(d - 2.0) < 1e-3 for d in cube.dimensions)
      and bpy.context.mode == 'SCULPT', (len(cube.data.polygons), tuple(cube.dimensions)))
far = float(np.linalg.norm(corners, axis=1).max())
check("its edges and corners are shaved off by a tenth of its half-width", abs(far - (0.9 * 3 ** 0.5 + 0.1)) < 1e-3
      and abs(float(np.abs(corners).max()) - 1.0) < 1e-5, far)
check("its quads are all about the same size", edges.max() < 1.45 * edges.min(), (float(edges.min()), float(edges.max())))
bpy.ops.sculptkit.new_sculpt(shape='CUBE', rounding=0.0, location=(9.0, 0.0, 0.0))
sharp = bpy.context.active_object
check("with no rounding it is a plain cube", abs(float(np.linalg.norm([v.co for v in sharp.data.vertices], axis=1).max()) - 3 ** 0.5) < 1e-4)
bpy.ops.sculptkit.new_sculpt(radius=0.7, location=(3.0, 0.0, 0.0))
given = bpy.context.active_object
check("a radius and a location that are given are kept", all(abs(d - 1.4) < 1e-3 for d in given.dimensions)
      and tuple(given.location) == (3.0, 0.0, 0.0), (tuple(given.dimensions), tuple(given.location)))
bpy.ops.sculptkit.new_sculpt(shape='CYLINDER', location=(12.0, 0.0, 0.0))
rod = bpy.context.active_object
co = np.array([v.co for v in rod.data.vertices])
rod_edges = np.array([(rod.data.vertices[e.vertices[0]].co - rod.data.vertices[e.vertices[1]].co).length
                      for e in rod.data.edges])
across = np.linalg.norm(co[:, :2], axis=1)
check("the cylinder stands 2 tall and 0.2 across, of quads", abs(rod.dimensions.z - 2.0) < 1e-3
      and abs(rod.dimensions.x - 0.2) < 1e-3 and all(len(p.vertices) == 4 for p in rod.data.polygons),
      (tuple(rod.dimensions), len(rod.data.polygons)))
check("it is round across, with rounded ends", abs(float(across[np.abs(co[:, 2]) < 0.85].min()) - 0.1) < 1e-4
      and float(across[np.abs(co[:, 2]) > 0.999].max()) < 0.02, float(across.max()))
check("its quads are all about the same size", rod_edges.max() < 1.6 * rod_edges.min(),
      (float(rod_edges.min()), float(rod_edges.max())))
import bmesh
closed = bmesh.new()
closed.from_mesh(rod.data)
check("its mesh is closed", all(len(e.link_faces) == 2 for e in closed.edges) and len(closed.verts) == len(co))
closed.free()
bpy.ops.sculptkit.new_sculpt(triangles=True, location=(15.0, 0.0, 0.0))
tris = bpy.context.active_object
check("Triangles gives the same sphere in triangles", len(tris.data.polygons) == 2 * 6 * 4 ** 6
      and all(len(p.vertices) == 3 for p in tris.data.polygons), len(tris.data.polygons))
bpy.ops.object.mode_set(mode='OBJECT')
for extra in (part, given, cube, sharp, rod, tris):
    bpy.data.objects.remove(extra)
ob.select_set(True)
bpy.context.view_layer.objects.active = ob
bpy.ops.object.mode_set(mode='SCULPT')

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
remesh_module = importlib.import_module(MODULE + ".features.remesh")
bpy.ops.mesh.primitive_monkey_add()
monkey = bpy.context.active_object
bpy.ops.object.modifier_add(type='SUBSURF')
bpy.ops.object.modifier_apply(modifier="Subdivision")
guess = remesh_module.estimate_faces(monkey.data, 128)
bpy.ops.sculptkit.voxel_remesh(resolution=128)
check("the panel's face estimate is within a fifth of what Voxel Remesh gives", abs(guess - len(monkey.data.polygons))
      < 0.2 * len(monkey.data.polygons), (guess, len(monkey.data.polygons)))
bpy.data.objects.remove(monkey)

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
      not all_kit_keys() and kit_library not in library_paths()
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
