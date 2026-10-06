import sys

import bpy

# The extension's package name, e.g. "bl_ext.user_default.sculpt_kit".
PACKAGE = __package__

# Sidebar tab every Sculpt Kit panel lives in.
PANEL_CATEGORY = "Sculpt Kit"


def prefs():
    """This add-on's preferences, or None while it is still being enabled."""
    addon = bpy.context.preferences.addons.get(PACKAGE)
    return addon.preferences if addon else None


def window_region(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def brush_tool_active(context):
    """True while the active Sculpt Mode tool is a brush. Box Mask, Lasso, the
    transform tools and the rest have their own use for a drag, and their own settings."""
    if context.mode != 'SCULPT':
        return False
    tool = context.workspace.tools.from_space_view3d_mode('SCULPT', create=False)
    return tool is not None and tool.use_brushes


def switching_all_addons_off():
    """True while Blender is switching every add-on off at once: on its way out, or to reload its preferences.

    Nothing more is saved after that point, so a feature that changed Blender's
    own settings must leave them as they are, in step with its saved switches.
    Blender gives add-ons no flag for this; the call it makes is recognisable.
    """
    frame = sys._getframe()
    while frame is not None:
        if frame.f_code.co_name == "disable_all" and frame.f_globals.get("__name__") == "addon_utils":
            return True
        frame = frame.f_back
    return False


def redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
