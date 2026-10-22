"""Subdivide.

A sidebar panel for subdivision levels, laid out like ZBrush's Geometry menu.
Divide adds a level (creating a Multires modifier the first time), Lower and
Higher step between levels, SDiv picks one, Delete Higher drops the levels
above the one shown, Apply bakes the level shown into the mesh and Remove
drops the levels altogether.

With no levels yet, Subdivide Mesh smooths the mesh into one level more
detail directly, with no modifier left behind: handy while still remeshing,
which Multires does not allow.

Divide is also on Ctrl + D with the ZBrush keys, so its operator is shared:
whichever of the two features is on registers it.
"""
import bpy

from .. import common

_divide_users = set()  # the features that have the Divide operator registered


def _multires(ob):
    return next((m for m in ob.modifiers if m.type == 'MULTIRES'), None)


class SCULPTKIT_OT_divide(bpy.types.Operator):
    """Add a Multires level, creating the modifier if the mesh has none (Ctrl D; D and Shift D step between levels)"""
    bl_idname = "sculptkit.divide"
    bl_label = "Divide"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'

    def execute(self, context):
        ob = context.active_object
        multires = _multires(ob)
        if multires is None:
            bpy.ops.object.modifier_add(type='MULTIRES')
            multires = _multires(ob)
        bpy.ops.object.multires_subdivide(modifier=multires.name, mode='CATMULL_CLARK')
        return {'FINISHED'}


def use_divide(user):
    if not _divide_users:
        bpy.utils.register_class(SCULPTKIT_OT_divide)
    _divide_users.add(user)


def release_divide(user):
    _divide_users.discard(user)
    if not _divide_users and SCULPTKIT_OT_divide.is_registered:
        bpy.utils.unregister_class(SCULPTKIT_OT_divide)


_ACTIONS = (('APPLY', "Apply", "Bake the level shown into the mesh and drop the modifier"),
            ('REMOVE', "Remove", "Drop every level and the detail on them, keeping the base mesh"))


class SCULPTKIT_OT_multires_finish(bpy.types.Operator):
    """Apply: bake the level shown into the mesh. Remove: drop every level, keeping the base mesh"""
    bl_idname = "sculptkit.multires_finish"
    bl_label = "Finish Levels"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=_ACTIONS)

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and _multires(ob) is not None

    @classmethod
    def description(cls, context, properties):
        return next(text for action, _name, text in _ACTIONS if action == properties.action)

    def execute(self, context):
        ob = context.active_object
        multires = _multires(ob)
        with common.object_mode(context):
            if self.action == 'APPLY':
                multires.levels = multires.sculpt_levels  # applying takes the viewport level; make it the one shown
                bpy.ops.object.modifier_apply(modifier=multires.name)
            else:
                bpy.ops.object.modifier_remove(modifier=multires.name)
        return {'FINISHED'}


class SCULPTKIT_OT_subdivide_mesh(bpy.types.Operator):
    """Smooth the mesh into four times the faces, with no modifier"""
    bl_idname = "sculptkit.subdivide_mesh"
    bl_label = "Subdivide Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and _multires(ob) is None

    def execute(self, context):
        ob = context.active_object
        with common.object_mode(context):
            modifier = ob.modifiers.new("Sculpt Kit Subdivide", 'SUBSURF')
            modifier.levels = 1
            bpy.ops.object.modifier_move_to_index(modifier=modifier.name, index=0)  # before any mirror and the like
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        self.report({'INFO'}, "Subdivided to {:,} faces".format(len(ob.data.polygons)))
        return {'FINISHED'}


class SCULPTKIT_PT_subdivide(bpy.types.Panel):
    bl_label = "Subdivide"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = common.PANEL_CATEGORY
    bl_order = 3

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'

    def draw(self, context):
        ob = context.active_object
        multires = _multires(ob)
        col = self.layout.column()
        if multires is None:
            row = col.row(align=True)
            row.operator(SCULPTKIT_OT_divide.bl_idname, text="Divide", icon='MOD_MULTIRES')
            row.operator(SCULPTKIT_OT_subdivide_mesh.bl_idname, text="Subdivide", icon='MOD_SUBSURF')
            common.info(col, "%s faces, no Multires levels" % common.face_count(len(ob.data.polygons)))
            return
        row = col.row(align=True)
        lower = row.operator("object.subdivision_set", text="", icon='TRIA_LEFT')
        lower.level, lower.relative, lower.ensure_modifier = -1, True, False
        row.prop(multires, "sculpt_levels", text="Level")
        higher = row.operator("object.subdivision_set", text="", icon='TRIA_RIGHT')
        higher.level, higher.relative, higher.ensure_modifier = 1, True, False
        row = col.row(align=True)
        row.operator(SCULPTKIT_OT_divide.bl_idname, text="Divide", icon='MOD_MULTIRES')
        row.operator("object.multires_higher_levels_delete", text="Delete Higher").modifier = multires.name
        row = col.row(align=True)
        row.operator(SCULPTKIT_OT_multires_finish.bl_idname, text="Apply").action = 'APPLY'
        row.operator(SCULPTKIT_OT_multires_finish.bl_idname, text="Remove").action = 'REMOVE'
        # Each level has four times the faces of the one below. (Blender keeps a Multires sculpt's own count
        # out of reach in Sculpt Mode, where the evaluated mesh reports only the base mesh.)
        faces = len(ob.data.polygons) * 4 ** multires.sculpt_levels
        common.info(col, "Level %d of %d, %s faces" % (multires.sculpt_levels, multires.total_levels,
                                                     common.face_count(faces)))


_classes = (SCULPTKIT_OT_multires_finish, SCULPTKIT_OT_subdivide_mesh, SCULPTKIT_PT_subdivide)


def enable():
    use_divide("subdivide")
    for cls in _classes:
        bpy.utils.register_class(cls)


def disable():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    release_divide("subdivide")
