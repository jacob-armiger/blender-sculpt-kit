"""Brush tuning helper for pen-testing rounds.

Adjust a brush while sculpting, press Copy Tuning, and paste the block into
tuning/brushes.toml. The next build bakes those values into the brush.
"""
import bpy

from .. import brush_settings, common


def _brush(context):
    return context.tool_settings.sculpt.brush if context.mode == 'SCULPT' else None


class SCULPTKIT_OT_copy_brush_tuning(bpy.types.Operator):
    """Copy the active brush's settings as a block for tuning/brushes.toml"""
    bl_idname = "sculptkit.copy_brush_tuning"
    bl_label = "Copy Tuning"

    @classmethod
    def poll(cls, context):
        return _brush(context) is not None

    def execute(self, context):
        brush = _brush(context)
        context.window_manager.clipboard = brush_settings.to_toml(brush.name, brush_settings.read(brush))
        self.report({'INFO'}, "Copied settings for %s" % brush.name)
        return {'FINISHED'}


class SCULPTKIT_PT_brush_tuning(bpy.types.Panel):
    bl_label = "Brush Tuning"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = common.PANEL_CATEGORY
    bl_order = 4

    @classmethod
    def poll(cls, context):
        return _brush(context) is not None

    def draw(self, context):
        brush = _brush(context)
        col = self.layout.column()
        col.label(text=brush.name, icon='BRUSH_DATA')
        col.prop(brush, "strength")
        col.prop(brush, "spacing")
        col.prop(brush, "hardness")
        col.prop(brush, "use_pressure_size", text="Pressure Changes Size")
        col.prop(brush, "use_pressure_strength", text="Pressure Changes Strength")
        col.operator(SCULPTKIT_OT_copy_brush_tuning.bl_idname, icon='COPYDOWN')


_classes = (SCULPTKIT_OT_copy_brush_tuning, SCULPTKIT_PT_brush_tuning)


def enable():
    for cls in _classes:
        bpy.utils.register_class(cls)


def disable():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
