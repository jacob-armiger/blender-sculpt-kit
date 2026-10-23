"""A one-time welcome that asks which parts of the kit to use.

Right after the kit is installed, a dialog lists its tools, the interface
parts, the colour theme and the keys, so someone who wants only the brushes, or
who already has a remesher they prefer, can leave the rest off. The theme and
keys change how Blender looks and behaves, so they start off until picked. It
is shown once; everything can be changed later in the add-on's preferences.
"""
import bpy
from bpy.props import BoolProperty, EnumProperty

from . import common

THEMES = (
    ('OFF', "Off", "Keep your own Blender theme"),
    ('KLAY', "KlaySculpt", "Slate panels over a night-blue canvas"),
    ('ZBRUSH', "ZBrush", "Dark panels, orange highlights, a black-to-grey canvas"),
)


# (operator property, preference switches it sets): the parts someone might want, each on its own.
TOOLS = (
    ("brushes", ("use_kit_brushes",)),
    ("new_sculpt", ("use_sculpt_session",)),
    ("subtools", ("look_subtools",)),
    ("subdivide", ("use_subdivide",)),
    ("remesh", ("use_remesh",)),
    ("reference", ("use_reference_view",)),
    ("interface", ("look_layout", "look_shelf", "look_material")),
)


def _start_with(self, _context):
    everything = self.start == 'ALL'
    for name, _switches in TOOLS:
        setattr(self, name, everything or name == "brushes")
    if not everything:
        self.theme = 'OFF'
        self.keys = self.keys_all_modes = False


class SCULPTKIT_OT_welcome(bpy.types.Operator):
    """Choose which parts of Sculpt Kit to use"""
    bl_idname = "sculptkit.welcome"
    bl_label = "Welcome to Sculpt Kit"
    bl_options = {'INTERNAL'}

    start: EnumProperty(name="Start With", update=_start_with, items=(
        ('ALL', "Everything", "Every tool on; the theme and keys as picked below"),
        ('BRUSHES', "Brushes Only", "Only the Sculpt Kit brushes; Blender otherwise as it was")))
    brushes: BoolProperty(name="Brushes", default=True, description="The Sculpt Kit tab in the brush shelf")
    new_sculpt: BoolProperty(name="New Sculpt", default=True,
                             description="Start a sculpt from an even sphere, cube or armature cylinder")
    subtools: BoolProperty(name="Subtools", default=True, description="A list of the scene's meshes to switch between")
    subdivide: BoolProperty(name="Subdivide", default=True, description="Quick access to Multires and subdivision")
    remesh: BoolProperty(name="Remesh", default=True,
                         description="Voxel remeshing by resolution, and Repairing Remesh: quad remeshing built for dense sculpts")
    reference: BoolProperty(name="Reference View", default=True,
                            description="Compare the sculpt side by side with a reference, at the same view and scale")
    interface: BoolProperty(name="ZBrush-style layout, brush bar and materials", default=True,
                            description="A ZBrush Layout button, the brush bar laid out like ZBrush's, and matcaps")
    theme: EnumProperty(name="Color Theme", items=THEMES, default='KLAY')
    keys: BoolProperty(name="ZBrush keys in Sculpt Mode", default=False,
                       description="Right-drag to rotate, Alt inverts the brush, Ctrl masks, and ZBrush's "
                                   "single-key shortcuts")
    keys_all_modes: BoolProperty(name="Right-drag navigation in every mode", default=False,
                                 description="Right-drag to rotate, Alt to move, Ctrl to zoom in Object and Edit "
                                             "Mode too; a right-click without dragging still opens Blender's menu")

    def invoke(self, context, _event):
        common.prefs().welcome_done = True  # shown once, however it is closed
        return context.window_manager.invoke_props_dialog(self, width=420, confirm_text="Done")

    def draw(self, _context):
        col = self.layout.column()
        col.row().prop(self, "start", expand=True)
        col.separator()
        col.label(text="Tools")
        grid = col.grid_flow(columns=2, even_columns=True)
        for name, _switches in TOOLS[:-1]:
            grid.prop(self, name)
        col.separator()
        col.label(text="Interface")
        col.prop(self, "interface")
        col.row().prop(self, "theme", expand=True)
        col.separator()
        col.label(text="Keys")
        col.prop(self, "keys")
        col.prop(self, "keys_all_modes")
        col.separator()
        common.info(col, "Change any of it later in Preferences > Add-ons > Sculpt Kit.")

    def execute(self, _context):
        prefs = common.prefs()
        prefs.welcome_done = True
        for name, switches in TOOLS:
            for switch in switches:
                setattr(prefs, switch, getattr(self, name))
        if self.theme != 'OFF':
            prefs.look_style = self.theme
        prefs.look_theme = self.theme != 'OFF'
        prefs.use_zbrush_look = self.subtools or self.interface or self.theme != 'OFF'
        prefs.keys_all_modes = self.keys_all_modes
        prefs.use_zbrush_keys = self.keys or self.keys_all_modes
        if self.keys_all_modes and not self.keys:
            # Only the navigation was wanted: the rest of the ZBrush keys stay as Blender has them.
            prefs.keys_canvas = prefs.keys_modifiers = prefs.keys_hotkeys = False
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
