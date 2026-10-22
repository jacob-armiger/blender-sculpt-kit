"""A one-time welcome that asks about the parts of the kit that change how Blender looks and behaves.

The colour theme restyles all of Blender and the ZBrush keys change Sculpt
Mode's controls, so both start off. Right after the kit is installed, a dialog
asks whether to use them. It is shown once; both can be changed any time in
the add-on's preferences.
"""
import bpy
from bpy.props import BoolProperty, EnumProperty

from . import common

THEMES = (
    ('OFF', "Off", "Keep your own Blender theme"),
    ('KLAY', "KlaySculpt", "Slate panels over a night-blue canvas"),
    ('ZBRUSH', "ZBrush", "Dark panels, orange highlights, a black-to-grey canvas"),
)


class SCULPTKIT_OT_welcome(bpy.types.Operator):
    """Choose a colour theme and whether to use the ZBrush keys"""
    bl_idname = "sculptkit.welcome"
    bl_label = "Welcome to Sculpt Kit"
    bl_options = {'INTERNAL'}

    theme: EnumProperty(name="Color Theme", items=THEMES, default='KLAY')
    keys: BoolProperty(name="ZBrush Keys", default=False,
                       description="In Sculpt Mode: right-drag to rotate, Alt inverts the brush, Ctrl masks, "
                                   "and ZBrush's single-key shortcuts")

    def invoke(self, context, _event):
        prefs = common.prefs()
        prefs.welcome_done = True  # shown once, however it is closed
        self.keys = prefs.use_zbrush_keys
        return context.window_manager.invoke_props_dialog(self, width=460, confirm_text="Done")

    def draw(self, _context):
        col = self.layout.column()
        col.label(text="Two optional parts change how Blender looks and behaves.")
        col.separator()
        col.label(text="Color theme for all of Blender:")
        col.row().prop(self, "theme", expand=True)
        col.separator()
        col.prop(self, "keys", text="Use ZBrush keys in Sculpt Mode")
        common.info(col, "Right-drag to rotate, Alt inverts, Ctrl masks, single-key shortcuts")
        col.separator()
        common.info(col, "Change either later in Preferences > Add-ons > Sculpt Kit.")

    def execute(self, _context):
        prefs = common.prefs()
        prefs.welcome_done = True
        if self.theme != 'OFF':
            prefs.look_style = self.theme
        prefs.look_theme = self.theme != 'OFF'
        prefs.use_zbrush_keys = self.keys
        return {'FINISHED'}


def _welcome():
    """Open the welcome once a window is there to show it in; never in the background."""
    if bpy.app.background:
        return None
    prefs = common.prefs()
    windows = bpy.context.window_manager.windows
    if prefs is None or not windows:
        return 0.5
    if prefs.welcome_done:
        return None
    window = windows[0]
    # A dialog opens over an area of the window; a timer has none of its own, so it is given the largest.
    area = max(window.screen.areas, key=lambda a: a.width * a.height)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
        bpy.ops.sculptkit.welcome('INVOKE_DEFAULT')
    return None


def register():
    bpy.utils.register_class(SCULPTKIT_OT_welcome)
    bpy.app.timers.register(_welcome, first_interval=1.0)


def unregister():
    if bpy.app.timers.is_registered(_welcome):
        bpy.app.timers.unregister(_welcome)
    bpy.utils.unregister_class(SCULPTKIT_OT_welcome)
