import sys
from contextlib import contextmanager

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


@contextmanager
def object_mode(context):
    """Object Mode for the length of the block, for operators that need it and so that what was sculpted is in
    the mesh. Then back to the mode before, if the active object can still take it."""
    ob = context.view_layer.objects.active
    mode = ob.mode if ob is not None else 'OBJECT'
    if mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        yield
    finally:
        if mode != 'OBJECT' and context.view_layer.objects.active is not None:
            try:
                bpy.ops.object.mode_set(mode=mode)
            except (RuntimeError, TypeError):
                pass  # the object cannot be put in that mode


def sculpt_keymap():
    """The add-on Sculpt Mode keymap, or None when Blender runs without one (in the background)."""
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    return None if keyconfig is None else keyconfig.keymaps.new(name="Sculpt", space_type='EMPTY', region_type='WINDOW')


def remove_keymap_items(added):
    """Take away the (keymap, item) pairs a feature added, and empty the list."""
    for keymap, item in added:
        try:
            keymap.keymap_items.remove(item)
        except (RuntimeError, ReferenceError):
            pass  # already gone with its keymap
    added.clear()


def face_count(n):
    """A face count as the panels show it: 1,234,567 as "1.2 M", 183,000 as "183 k"."""
    if n >= 1_000_000:
        return "%.1f M" % (n / 1_000_000)
    if n >= 10_000:
        return "%d k" % round(n / 1000)
    return "{:,}".format(n)


def info(layout, text, icon='NONE'):
    """A line of status in a panel, dimmed so that it reads apart from the buttons and settings around it."""
    row = layout.row()
    row.active = False
    row.label(text=text, icon=icon)


def warning(layout, text):
    layout.label(text=text, icon='ERROR')
