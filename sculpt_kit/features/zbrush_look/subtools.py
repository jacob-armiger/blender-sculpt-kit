"""Subtool list: the scene's meshes, shown the way ZBrush lists a tool's subtools.

Click a name to work on that mesh: the mode you are in moves over to it, so in
Sculpt Mode you carry on sculpting. The eye hides or shows a mesh. Duplicate,
Merge Down and Delete act on the active one.
"""
from contextlib import contextmanager

import bpy
from bpy.props import BoolProperty, StringProperty

from ... import common


def subtools(context):
    """The view layer's meshes, in the order the list shows them."""
    return sorted((ob for ob in context.view_layer.objects if ob.type == 'MESH'), key=lambda ob: ob.name.lower())


def _active(context):
    return context.view_layer.objects.active


def _enter(mode):
    try:
        bpy.ops.object.mode_set(mode=mode)
    except (RuntimeError, TypeError):
        pass  # the object cannot be put in that mode


@contextmanager
def _object_mode(context):
    """Object Mode for the length of the block, so that what was sculpted is in the mesh. Then back."""
    ob = _active(context)
    mode = ob.mode if ob is not None else 'OBJECT'
    if mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        yield
    finally:
        if mode != 'OBJECT' and _active(context) is not None:
            _enter(mode)


def _show(ob):
    ob.hide_viewport = False
    ob.hide_set(False)
    return ob.visible_get()


def _make_active(context, ob):
    for other in context.selected_objects:
        other.select_set(False)
    ob.select_set(True)
    context.view_layer.objects.active = ob


class SCULPTKIT_OT_subtool_select(bpy.types.Operator):
    """Work on this mesh. The mode you are in moves over to it"""
    bl_idname = "sculptkit.subtool_select"
    bl_label = "Select Subtool"
    bl_options = {'UNDO'}

    name: StringProperty()

    def execute(self, context):
        ob = context.view_layer.objects.get(self.name)
        current = _active(context)
        if ob is None or ob.type != 'MESH' or ob == current:
            return {'CANCELLED'}
        mode = current.mode if current is not None else 'OBJECT'
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if not _show(ob):
            if mode != 'OBJECT':
                _enter(mode)
            self.report({'WARNING'}, "%s is in a hidden collection" % ob.name)
            return {'CANCELLED'}
        _make_active(context, ob)
        if mode != 'OBJECT':
            _enter(mode)
        return {'FINISHED'}


class SCULPTKIT_OT_subtool_show(bpy.types.Operator):
    """Hide or show this mesh. Shift + click hides or shows all the others instead.
The mesh you are working on always stays visible"""
    bl_idname = "sculptkit.subtool_show"
    bl_label = "Hide or Show Subtool"
    bl_options = {'UNDO'}

    name: StringProperty()
    others: BoolProperty(name="All The Others", default=False, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        self.others = event.shift
        return self.execute(context)

    def execute(self, context):
        ob = context.view_layer.objects.get(self.name)
        if ob is None:
            return {'CANCELLED'}
        current = _active(context)
        if self.others:
            others = [other for other in subtools(context) if other not in (ob, current)]
            hide = any(other.visible_get() for other in others)
            for other in others:
                if hide:
                    other.hide_set(True)
                else:
                    _show(other)
            return {'FINISHED'}
        if ob.visible_get():
            if ob == current:
                return {'CANCELLED'}
            ob.hide_set(True)
        elif not _show(ob):
            self.report({'WARNING'}, "%s is in a hidden collection" % ob.name)
        return {'FINISHED'}


class _ActiveSubtool:
    @classmethod
    def poll(cls, context):
        ob = _active(context)
        return ob is not None and ob.type == 'MESH'


class SCULPTKIT_OT_subtool_duplicate(_ActiveSubtool, bpy.types.Operator):
    """Add a copy of the active mesh to the list"""
    bl_idname = "sculptkit.subtool_duplicate"
    bl_label = "Duplicate Subtool"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ob = _active(context)
        with _object_mode(context):
            copy = ob.copy()
            copy.data = ob.data.copy()
            for collection in ob.users_collection:
                collection.objects.link(copy)
        return {'FINISHED'}


class SCULPTKIT_OT_subtool_merge_down(_ActiveSubtool, bpy.types.Operator):
    """Join the mesh below the active one in the list into it"""
    bl_idname = "sculptkit.subtool_merge_down"
    bl_label = "Merge Subtool Down"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and _active(context) != subtools(context)[-1]

    def execute(self, context):
        ob = _active(context)
        listed = subtools(context)
        below = listed[listed.index(ob) + 1]
        with _object_mode(context):
            if not _show(below):
                self.report({'WARNING'}, "%s is in a hidden collection" % below.name)
                return {'CANCELLED'}
            _make_active(context, ob)
            below.select_set(True)
            result = bpy.ops.object.join()
        if 'FINISHED' not in result:
            self.report({'ERROR'}, "Could not merge %s into %s" % (below.name, ob.name))
            return {'CANCELLED'}
        return {'FINISHED'}


class SCULPTKIT_OT_subtool_delete(_ActiveSubtool, bpy.types.Operator):
    """Delete the active mesh. The next one in the list takes over"""
    bl_idname = "sculptkit.subtool_delete"
    bl_label = "Delete Subtool"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        ob = _active(context)
        listed = subtools(context)
        index = listed.index(ob)
        listed.remove(ob)
        successor = listed[min(index, len(listed) - 1)] if listed else None
        mode = ob.mode
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        mesh = ob.data
        bpy.data.objects.remove(ob)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        if successor is not None and _show(successor):
            _make_active(context, successor)
            if mode != 'OBJECT':
                _enter(mode)
        return {'FINISHED'}


class SCULPTKIT_PT_subtools(bpy.types.Panel):
    bl_label = "Subtools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = common.PANEL_CATEGORY
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        listed = subtools(context)
        if not listed:
            layout.label(text="No meshes in this scene yet")
            return
        current = _active(context)
        col = layout.column(align=True)
        for ob in listed:
            row = col.row(align=True)
            row.operator(SCULPTKIT_OT_subtool_select.bl_idname, text=ob.name, depress=ob == current).name = ob.name
            row.operator(SCULPTKIT_OT_subtool_show.bl_idname, text="",
                         icon='HIDE_OFF' if ob.visible_get() else 'HIDE_ON').name = ob.name
        if current is not None and current.type == 'MESH':
            col = layout.column(align=True)
            col.prop(current, "name", text="")
            row = col.row(align=True)
            row.operator(SCULPTKIT_OT_subtool_duplicate.bl_idname, text="Duplicate")
            row.operator(SCULPTKIT_OT_subtool_merge_down.bl_idname, text="Merge Down")
            row.operator(SCULPTKIT_OT_subtool_delete.bl_idname, text="Delete")


_classes = (SCULPTKIT_OT_subtool_select, SCULPTKIT_OT_subtool_show, SCULPTKIT_OT_subtool_duplicate,
            SCULPTKIT_OT_subtool_merge_down, SCULPTKIT_OT_subtool_delete, SCULPTKIT_PT_subtools)


def enable(by_user=False):
    for cls in _classes:
        bpy.utils.register_class(cls)


def disable(restore=True):
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
