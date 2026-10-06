"""ZBrush layout: a workspace with one big canvas and the Sculpt Kit tray beside it.

The ZBrush Layout button in the top bar switches to it, and pressing it again
goes back to where you were. Workspaces are saved inside the .blend file, so
the first press in a file builds one there: Blender's own Sculpting workspace
with the editors down its side closed. Switching the part off takes that
workspace out of the open file again.
"""
import bpy

from ... import common
from .. import sculpt_session

WORKSPACE = "Sculpt Kit"
MARK = "sculpt_kit_layout"      # custom property on the workspace the kit built
PREVIOUS = "sculpt_kit_previous"  # custom property: the workspace to go back to
STARTUP = "<startup.blend>"     # Blender's name for the startup file built into it
STEP = 0.05                     # seconds between tries while a workspace switch is still on its way
TRIES = 100

_on = False


def find():
    return next((workspace for workspace in bpy.data.workspaces if workspace.get(MARK)), None)


def _previous(kit):
    """The workspace to go back to from the kit's."""
    back = bpy.data.workspaces.get(kit.get(PREVIOUS, ""))
    if back is None or back == kit:
        back = next((workspace for workspace in bpy.data.workspaces if workspace != kit), kit)
    return back


def _canvas(screen):
    views = [area for area in screen.areas if area.type == 'VIEW_3D']
    return max(views, key=lambda area: area.width * area.height) if views else None


def _view_of(window):
    """Where the window's main viewport is looking from, to carry over to the new canvas."""
    area = _canvas(window.screen)
    if area is None:
        return None
    rv3d = area.spaces.active.region_3d
    return {"matrix": rv3d.view_matrix.copy(), "distance": rv3d.view_distance, "perspective": rv3d.is_perspective}


def _build():
    """Add the kit's workspace to this file. Blender switches to a new workspace by itself, a moment later."""
    before = set(bpy.data.workspaces.keys())
    try:
        bpy.ops.workspace.append_activate(idname="Sculpting", filepath=STARTUP)
    except RuntimeError:
        pass
    added = [workspace for workspace in bpy.data.workspaces if workspace.name not in before]
    if not added:
        bpy.ops.workspace.duplicate()  # no Sculpting workspace to be had: start from the one on screen
        added = [workspace for workspace in bpy.data.workspaces if workspace.name not in before]
    if not added:
        return None
    kit = added[0]
    kit.name = WORKSPACE
    kit[MARK] = True
    kit.object_mode = 'SCULPT'
    for screen in kit.screens:
        screen.name = WORKSPACE
    return kit


def _set_up(space, view):
    space.show_region_header = True
    space.show_region_tool_header = True
    space.show_region_toolbar = True
    space.show_region_ui = True
    try:
        space.show_region_asset_shelf = True
    except AttributeError:
        pass  # read-only outside the modes that have a brush shelf
    sculpt_session.setup_view(space)
    space.overlay.show_stats = True  # point and face counts, where ZBrush shows ActivePoints
    if view is not None:
        rv3d = space.region_3d
        rv3d.view_perspective = 'PERSP' if view["perspective"] else 'ORTHO'
        rv3d.view_distance = view["distance"]
        rv3d.view_matrix = view["matrix"]


def _finish_later(window, view):
    """Once the new workspace is on screen: close its side editors, set the canvas up, open the kit's tab."""
    pointer = window.as_pointer()
    state = {"tries": TRIES, "canvas_ready": False}

    def step():
        window = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        kit = find()
        state["tries"] -= 1
        if window is None or kit is None or state["tries"] < 0:
            return None
        if window.workspace != kit:
            return STEP
        canvas = _canvas(window.screen)
        if canvas is None:
            return None
        if not state["canvas_ready"]:
            for area in [area for area in window.screen.areas if area != canvas]:
                with bpy.context.temp_override(window=window, area=area, region=common.window_region(area)):
                    try:
                        bpy.ops.screen.area_close()
                    except RuntimeError:
                        pass
            # A timer runs outside any window, and showing or hiding a region needs to know which one it is in.
            with bpy.context.temp_override(window=window, area=canvas, region=common.window_region(canvas)):
                _set_up(canvas.spaces.active, view)
            state["canvas_ready"] = True
            return STEP
        tray = next(region for region in canvas.regions if region.type == 'UI')
        try:
            with bpy.context.temp_override(window=window, area=canvas, region=common.window_region(canvas)):
                tray.active_panel_category = common.PANEL_CATEGORY
        except TypeError:
            return STEP  # the tray has not been drawn yet, so Blender does not know its tabs
        canvas.tag_redraw()
        return None

    bpy.app.timers.register(step, first_interval=0.0)


def _remove():
    """Take the kit's workspace out of the open file, once no window is showing it."""
    state = {"tries": TRIES}

    def step():
        kit = find()
        state["tries"] -= 1
        if kit is None or _on or state["tries"] < 0:
            return None
        showing = [window for window in bpy.context.window_manager.windows if window.workspace == kit]
        if showing:
            back = _previous(kit)
            if back == kit:
                return None  # it is the only workspace in the file
            for window in showing:
                window.workspace = back
            return STEP
        # By datablock, not with Blender's Delete Workspace operator: that one deletes
        # whichever workspace is on screen. The screen goes too, or it is left behind unused.
        bpy.data.batch_remove(ids=[kit] + list(kit.screens))
        return None

    bpy.app.timers.register(step, first_interval=0.0)


class SCULPTKIT_OT_zbrush_layout(bpy.types.Operator):
    """Switch to the Sculpt Kit workspace: one big canvas with the Sculpt Kit tray beside it.
Press again to go back to the workspace you came from"""
    bl_idname = "sculptkit.zbrush_layout"
    bl_label = "ZBrush Layout"

    @classmethod
    def poll(cls, context):
        return context.window is not None

    def execute(self, context):
        window = context.window
        kit = find()
        if kit is not None and window.workspace == kit:
            window.workspace = _previous(kit)
            return {'FINISHED'}
        came_from = window.workspace
        # Blender changes mode on a change of workspace when the two workspaces' modes differ. Record the
        # mode in use on the one being left: the kit's workspace then starts in Sculpt Mode whatever that
        # was, and going back returns to it.
        ob = context.view_layer.objects.active
        try:
            came_from.object_mode = ob.mode if ob is not None else 'OBJECT'
        except TypeError:
            pass
        if kit is None:
            view = _view_of(window)
            kit = _build()
            if kit is None:
                self.report({'ERROR'}, "Could not add a workspace to this file")
                return {'CANCELLED'}
            _finish_later(window, view)
        else:
            window.workspace = kit
        kit[PREVIOUS] = came_from.name
        return {'FINISHED'}


def _top_bar_button(self, context):
    if context.region.alignment != 'RIGHT':
        return
    kit = find()
    self.layout.operator(SCULPTKIT_OT_zbrush_layout.bl_idname, icon='SCULPTMODE_HLT',
                         depress=kit is not None and context.window.workspace == kit)


def enable(by_user=False):
    global _on
    _on = True
    bpy.utils.register_class(SCULPTKIT_OT_zbrush_layout)
    bpy.types.TOPBAR_HT_upper_bar.prepend(_top_bar_button)


def disable(restore=True):
    global _on
    _on = False
    bpy.types.TOPBAR_HT_upper_bar.remove(_top_bar_button)
    bpy.utils.unregister_class(SCULPTKIT_OT_zbrush_layout)
    if restore and not bpy.app.background and find() is not None:
        _remove()
