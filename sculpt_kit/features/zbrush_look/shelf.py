"""ZBrush shelf.

In Sculpt Mode, with a brush active, the bar of brush settings above the canvas
is laid out like ZBrush's shelves: the Brush, Stroke and Alpha pickers that
ZBrush keeps on its left shelf, then Zadd / Zsub, Z Intensity and Draw Size
from its top shelf. The viewport header gains the view switches from ZBrush's
right shelf (Persp, Floor, PolyF, Transp, Solo) and a material picker.

While the invert key is held (Alt with the kit's ZBrush keys, otherwise Ctrl),
Zadd / Zsub light the other way, as a hint of what a stroke will do. The
brush's own setting is left alone. Blender's Python cannot ask which keys are
down, so a pass-through Sculpt Mode key binding notes the modifier keys from
their own presses and releases and from every mouse move, and redraws the bar
when they change.

Blender has no hook for rearranging that bar, so its own draw function is
swapped for ours, in the list Blender draws the bar from, while the part is on.
Anything that is not a sculpt brush is handed straight back to Blender's
function.
"""
import bpy
from bpy.props import EnumProperty

from ... import common
from . import material

_blender_draw = None  # Blender's own draw function for the bar, while ours stands in for it
_held = {}  # window pointer -> (alt, ctrl) as last seen in that window
_keymap_items = []
_MODIFIER_KEYS = {'LEFT_ALT': "alt", 'RIGHT_ALT': "alt", 'LEFT_CTRL': "ctrl", 'RIGHT_CTRL': "ctrl"}


def _invert_held(window):
    """Whether the key that inverts a stroke is held down in this window, and nothing that would make it mask."""
    from ... import features
    alt, ctrl = _held.get(window.as_pointer(), (False, False))
    prefs = common.prefs()
    if features.is_enabled("zbrush_keys") and prefs is not None and prefs.keys_modifiers:
        return alt and not ctrl  # Ctrl + Alt masks
    return ctrl and not alt


class SCULPTKIT_OT_watch_modifiers(bpy.types.Operator):
    """Notes which modifier keys are down, so Zadd / Zsub can show what a stroke will do"""
    bl_idname = "sculptkit.watch_modifiers"
    bl_label = "Watch Modifier Keys"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        window = context.window
        alt, ctrl = event.alt, event.ctrl
        side = _MODIFIER_KEYS.get(event.type)
        if side is not None:  # the key's own press or release says more surely than the flags on it
            down = event.value == 'PRESS'
            alt, ctrl = (down, ctrl) if side == "alt" else (alt, down)
        if window is not None and _held.get(window.as_pointer(), (False, False)) != (alt, ctrl):
            _held[window.as_pointer()] = (alt, ctrl)
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    for region in area.regions:
                        if region.type == 'TOOL_HEADER':
                            region.tag_redraw()
        return {'PASS_THROUGH'}


def _shelf(self, context):
    paint = context.tool_settings.sculpt
    brush = paint.brush
    if brush is None or not common.brush_tool_active(context):
        _blender_draw(self, context)
        return
    from bl_ui.properties_paint_common import BrushAssetShelf, UnifiedPaintPanel
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper

    layout = self.layout
    ToolSelectPanelHelper.draw_active_tool_header(context, layout, tool_key=('VIEW_3D', 'SCULPT'))
    capabilities = brush.sculpt_capabilities
    unified = paint.unified_paint_settings

    # ZBrush's left shelf.
    BrushAssetShelf.draw_popup_selector(layout, context, brush)
    layout.popover("VIEW3D_PT_tools_brush_stroke", text="Stroke")
    layout.popover("VIEW3D_PT_tools_brush_texture", text="Alpha")

    if capabilities.has_color:
        row = layout.row(align=True)
        row.ui_units_x = 4
        UnifiedPaintPanel.prop_unified_color(row, context, brush, "color", text="")
        UnifiedPaintPanel.prop_unified_color(row, context, brush, "secondary_color", text="")
        row.separator()
        layout.prop(brush, "blend", text="", expand=False)

    # ZBrush's top shelf. Widths are set to keep the whole bar within a few buttons of the length of Blender's own.
    if capabilities.has_direction:
        row = layout.row(align=True)
        if brush.direction in {'ADD', 'SUBTRACT'}:
            row.ui_units_x = 4.6
            lit = brush.direction
            if _invert_held(context.window):
                lit = 'SUBTRACT' if lit == 'ADD' else 'ADD'
            for value, label in (('ADD', "Zadd"), ('SUBTRACT', "Zsub")):
                row.operator(SCULPTKIT_OT_brush_direction.bl_idname, text=label,
                             depress=lit == value).direction = value
        else:
            row.prop(brush, "direction", expand=True, text="")  # Flatten, Scrape and others name the two their own way

    row = UnifiedPaintPanel.prop_unified(
        layout, context, brush, "strength",
        pressure_name="use_pressure_strength" if capabilities.has_strength_pressure else None,
        unified_name="use_unified_strength", text="Z Intensity", header=True)
    row.ui_units_x = 7.8

    size_owner = unified if unified.use_unified_size else brush
    row = UnifiedPaintPanel.prop_unified(
        layout, context, brush, "unprojected_size" if size_owner.use_locked_size == 'SCENE' else "size",
        pressure_name="use_pressure_size", unified_name="use_unified_size", text="Draw Size", slider=True, header=True)
    row.ui_units_x = 7.2

    row = layout.row()
    row.ui_units_x = 6.0
    row.prop(brush, "hardness", slider=True)
    layout.popover("VIEW3D_PT_tools_brush_falloff")
    layout.popover("VIEW3D_PT_tools_brush_settings_advanced", text="Brush")
    # Blender's Cursor popover is left out to make room. Its settings are in the sidebar's Tool tab.

    layout.separator_spacer()
    self.draw_mode_settings(context)


def _view_switches(self, context):
    if context.mode != 'SCULPT':
        return
    space = context.space_data
    overlay = space.overlay
    row = self.layout.row(align=True)
    row.ui_units_x = 11.0
    row.operator("view3d.view_persportho", text="Persp", depress=space.region_3d.is_perspective)
    row.prop(overlay, "show_floor", text="Floor", toggle=True)
    row.prop(overlay, "show_wireframes", text="PolyF", toggle=True)
    row.prop(overlay, "show_fade_inactive", text="Transp", toggle=True)
    row.operator("view3d.localview", text="Solo", depress=space.local_view is not None).frame_selected = False
    self.layout.popover(SCULPTKIT_PT_material.__name__, text="", icon='MATSPHERE')


class SCULPTKIT_OT_brush_direction(bpy.types.Operator):
    """Whether the brush adds to the surface or takes away from it"""
    bl_idname = "sculptkit.brush_direction"
    bl_label = "Brush Direction"
    bl_options = {'INTERNAL'}  # no undo step, the same as changing any other brush setting

    direction: EnumProperty(items=(('ADD', "Zadd", "Build the surface up"), ('SUBTRACT', "Zsub", "Carve into it")))

    @classmethod
    def poll(cls, context):
        return context.mode == 'SCULPT' and context.tool_settings.sculpt.brush is not None

    def execute(self, context):
        context.tool_settings.sculpt.brush.direction = self.direction
        return {'FINISHED'}


class SCULPTKIT_PT_material(bpy.types.Panel):
    """What the model is shaded with while sculpting"""
    bl_label = "Material"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 11

    def draw(self, context):
        shading = context.space_data.shading
        col = self.layout.column()
        if shading.type != 'SOLID':
            col.label(text="Materials show in Solid shading")
            col.prop(shading, "type", expand=True)
            return
        if material.matcap() is not None:
            # The kit's own three, one click each. The one this viewport shows is lit.
            showing = shading.studio_light if shading.light == 'MATCAP' else None
            row = col.row(align=True)
            for identifier, label, _description in material.MATERIALS:
                row.operator(material.SCULPTKIT_OT_material.bl_idname, text=label,
                             depress=showing == material.MATCAPS[identifier]).material = identifier
            col.separator()
        col.row().prop(shading, "light", expand=True)
        if shading.light != 'FLAT':
            col.template_icon_view(shading, "studio_light", scale=6.0, scale_popup=3.0)
        if shading.light == 'MATCAP':
            col.operator("view3d.toggle_matcap_flip", icon='ARROW_LEFTRIGHT')
        col.prop(shading, "show_cavity")


_classes = (SCULPTKIT_OT_brush_direction, SCULPTKIT_OT_watch_modifiers, SCULPTKIT_PT_material)


def _bar_functions():
    """The list Blender draws the bar from: its own function, plus whatever add-ons have added around it."""
    return bpy.types.VIEW3D_HT_tool_header._dyn_ui_initialize()


def in_place():
    return _shelf in _bar_functions()


def enable(by_user=False):
    global _blender_draw
    functions = _bar_functions()
    own = next((f for f in functions if getattr(f, "__module__", "") == "bl_ui.space_view3d"), None)
    if own is not None:  # otherwise something else has already replaced it, and the bar is left alone
        _blender_draw = own
        functions[functions.index(own)] = _shelf
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_HT_header.append(_view_switches)
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is not None:
        keymap = keyconfig.keymaps.new("Sculpt", space_type='EMPTY')
        for key, value in [('MOUSEMOVE', 'ANY')] + [(k, v) for k in _MODIFIER_KEYS for v in ('PRESS', 'RELEASE')]:
            _keymap_items.append((keymap, keymap.keymap_items.new(
                SCULPTKIT_OT_watch_modifiers.bl_idname, key, value, any=True)))
    common.redraw_all()


def disable(restore=True):
    global _blender_draw
    functions = _bar_functions()
    if _shelf in functions:
        functions[functions.index(_shelf)] = _blender_draw
    _blender_draw = None
    bpy.types.VIEW3D_HT_header.remove(_view_switches)
    for keymap, item in _keymap_items:
        try:
            keymap.keymap_items.remove(item)
        except (ReferenceError, RuntimeError):
            pass
    _keymap_items.clear()
    _held.clear()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    if restore:
        common.redraw_all()
