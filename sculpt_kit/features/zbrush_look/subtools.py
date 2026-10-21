"""Subtool list: the scene's meshes, shown the way ZBrush lists a tool's subtools.

Click a name to work on that mesh: the mode you are in moves over to it, so in
Sculpt Mode you carry on sculpting. The eye hides or shows a mesh. Duplicate,
Merge Down and Delete act on the active one, and the arrows move it up or down
the list. The list keeps the order it is given; meshes that have not been
moved yet follow, by name.

As in ZBrush, the mesh being worked on is always in view. Its eye can still be
switched off: the mesh then goes into hiding as soon as another takes over, and
comes back, eye still off, for as long as it is picked again.
"""
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty

from ... import common

EYE_OFF = "sculpt_kit_eye_off"  # on the mesh being worked on while its eye is off
ORDER = "sculpt_kit_order"  # a mesh's place in the list, once it has been moved; a copy takes its original's


def subtools(context):
    """The view layer's meshes, in the order the list shows them."""
    meshes = (ob for ob in context.view_layer.objects if ob.type == 'MESH')
    return sorted(meshes, key=lambda ob: (ORDER not in ob, ob.get(ORDER, 0), ob.name.lower()))


def _active(context):
    return context.view_layer.objects.active


def _enter(mode):
    try:
        bpy.ops.object.mode_set(mode=mode)
    except (RuntimeError, TypeError):
        pass  # the object cannot be put in that mode


def _show(ob):
    ob.hide_viewport = False
    ob.hide_set(False)
    return ob.visible_get()


def _make_active(context, ob):
    for other in context.selected_objects:
        other.select_set(False)
    ob.select_set(True)
    context.view_layer.objects.active = ob


def eye_off(ob, current):
    """Whether the list shows this mesh's eye as off."""
    return not ob.visible_get() or (ob == current and bool(ob.get(EYE_OFF)))


def settle(view_layer):
    """Hide the meshes whose eye was switched off while they were worked on, now that another has taken over."""
    current = view_layer.objects.active
    for ob in view_layer.objects:
        if ob != current and ob.get(EYE_OFF):
            del ob[EYE_OFF]
            ob.hide_set(True, view_layer=view_layer)


def _work_on(context, ob):
    """Make this mesh the one being worked on, which brings it into view. False if it cannot be shown."""
    off = not ob.visible_get()
    if not _show(ob):
        return False
    _make_active(context, ob)
    if off:
        ob[EYE_OFF] = True
    settle(context.view_layer)
    return True


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
        if not _work_on(context, ob):
            if mode != 'OBJECT':
                _enter(mode)
            self.report({'WARNING'}, "%s is in a hidden collection" % ob.name)
            return {'CANCELLED'}
        if mode != 'OBJECT':
            _enter(mode)
        return {'FINISHED'}


class SCULPTKIT_OT_subtool_show(bpy.types.Operator):
    """Hide or show this mesh. Shift + click hides or shows all the others instead.
The mesh you are working on stays in view: with its eye off, it hides when you pick another"""
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
        if not ob.visible_get():
            if not _show(ob):
                self.report({'WARNING'}, "%s is in a hidden collection" % ob.name)
            ob.pop(EYE_OFF, None)
        elif ob != current:
            ob.pop(EYE_OFF, None)
            ob.hide_set(True)
        elif ob.pop(EYE_OFF, None) is None:
            ob[EYE_OFF] = True
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
        with common.object_mode(context):
            copy = ob.copy()
            copy.data = ob.data.copy()
            copy.pop(EYE_OFF, None)
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
        with common.object_mode(context):
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


class SCULPTKIT_OT_subtool_move(_ActiveSubtool, bpy.types.Operator):
    """Move the active mesh up or down the list"""
    bl_idname = "sculptkit.subtool_move"
    bl_label = "Move Subtool"
    bl_options = {'UNDO'}

    direction: bpy.props.EnumProperty(items=(('UP', "Up", "Move it up the list"),
                                             ('DOWN', "Down", "Move it down the list")))

    @classmethod
    def description(cls, context, properties):
        return "Move the active mesh %s the list" % properties.direction.lower()

    def execute(self, context):
        listed = subtools(context)
        index = listed.index(_active(context))
        other = index - 1 if self.direction == 'UP' else index + 1
        if not 0 <= other < len(listed):
            return {'CANCELLED'}
        listed[index], listed[other] = listed[other], listed[index]
        for place, ob in enumerate(listed):  # from now on every mesh keeps its place
            ob[ORDER] = place
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
        if successor is not None and _work_on(context, successor):
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
                         icon='HIDE_ON' if eye_off(ob, current) else 'HIDE_OFF').name = ob.name
        if current is not None and current.type == 'MESH':
            col = layout.column(align=True)
            row = col.row(align=True)
            row.prop(current, "name", text="")
            first, last = listed.index(current) == 0, listed.index(current) == len(listed) - 1
            sub = row.row(align=True)
            sub.enabled = not first
            sub.operator(SCULPTKIT_OT_subtool_move.bl_idname, text="", icon='TRIA_UP').direction = 'UP'
            sub = row.row(align=True)
            sub.enabled = not last
            sub.operator(SCULPTKIT_OT_subtool_move.bl_idname, text="", icon='TRIA_DOWN').direction = 'DOWN'
            row = col.row(align=True)
            row.operator(SCULPTKIT_OT_subtool_duplicate.bl_idname, text="Duplicate")
            row.operator(SCULPTKIT_OT_subtool_merge_down.bl_idname, text="Merge Down")
            row.operator(SCULPTKIT_OT_subtool_delete.bl_idname, text="Delete")


_classes = (SCULPTKIT_OT_subtool_select, SCULPTKIT_OT_subtool_show, SCULPTKIT_OT_subtool_duplicate,
            SCULPTKIT_OT_subtool_merge_down, SCULPTKIT_OT_subtool_move, SCULPTKIT_OT_subtool_delete,
            SCULPTKIT_PT_subtools)


# Blender has ways of its own to move on to another mesh: a click in Object Mode, Transfer Mode, the Outliner.
# None of them tells add-ons, so the result is looked for after each change to the scene.
_seen = {}  # view layer -> the active object when its meshes were last looked over


def _settle_all():
    for window in bpy.context.window_manager.windows:
        settle(window.view_layer)
    common.redraw_all()


@persistent
def _watch(_scene, depsgraph):
    if depsgraph.mode != 'VIEWPORT':
        return
    view_layer = depsgraph.view_layer
    current = view_layer.objects.active
    now = current.as_pointer() if current is not None else 0
    if _seen.get(view_layer.as_pointer()) == now:
        return
    _seen[view_layer.as_pointer()] = now
    if any(ob != current and ob.get(EYE_OFF) for ob in view_layer.objects):
        bpy.app.timers.register(_settle_all, first_interval=0.0)  # not from in here: Blender is in mid-update


def enable(by_user=False):
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.app.handlers.depsgraph_update_post.append(_watch)


def disable(restore=True):
    bpy.app.handlers.depsgraph_update_post.remove(_watch)
    _seen.clear()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
