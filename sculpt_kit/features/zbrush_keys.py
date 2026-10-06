"""ZBrush-style navigation and hotkeys for Sculpt Mode.

Everything is added to Blender's add-on keyconfig in the Sculpt keymap, so it
applies in Sculpt Mode only and disappears when the feature is switched off.
Add-on keymap items are tried before Blender's own, which is how these take
over a key without editing the default keymap.

Empty-canvas navigation relies on `sculpt.brush_stroke`'s own
`ignore_background_click` option: a click that misses the model is passed on
to the next binding for that key, which is the navigation operator.
"""
import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty

from .. import common

# Preference switch -> the lines shown under it in the add-on preferences.
CHEAT_SHEET = (
    ("keys_right_click", (
        "Right-drag: rotate",
        "Alt + right-drag: move",
        "Ctrl + right-drag: zoom",
        "Space: Blender's right-click sculpt menu",
    )),
    ("keys_canvas", (
        "Drag on empty canvas: rotate",
        "Alt + drag on empty canvas: move",
    )),
    ("keys_modifiers", (
        "Alt + drag: invert the brush",
        "Ctrl + drag on the model: paint mask (Ctrl + Alt: erase mask)",
        "Ctrl + click on empty canvas: invert mask",
        "Ctrl + drag from empty canvas: box mask, or clear the mask if the box misses the model",
    )),
    ("keys_hotkeys", (
        "S brush size, U strength",
        "F frame, Shift + F wireframe, P perspective",
        "X symmetry, B brush picker",
        "D / Shift + D subdivision level up / down, Ctrl + D divide",
    )),
)

_added = []  # (keymap, keymap item) pairs this feature created


# ------------------------------------------------------------------ operators

class SCULPTKIT_OT_canvas_navigate(bpy.types.Operator):
    """Navigate the view with a drag that started on empty canvas"""
    bl_idname = "sculptkit.canvas_navigate"
    bl_label = "Canvas Navigation"
    bl_options = {'INTERNAL'}

    action: EnumProperty(items=(('ROTATE', "Rotate", ""), ('MOVE', "Move", ""), ('ZOOM', "Zoom", "")))

    @classmethod
    def poll(cls, context):
        return common.brush_tool_active(context)

    def invoke(self, _context, _event):
        navigate = {'ROTATE': bpy.ops.view3d.rotate, 'MOVE': bpy.ops.view3d.move, 'ZOOM': bpy.ops.view3d.zoom}
        navigate[self.action]('INVOKE_DEFAULT')
        return {'FINISHED'}


def _box_misses_model(context, xmin, xmax, ymin, ymax):
    """True if no vertex of the active object lies inside the screen-space box."""
    ob = context.active_object
    mesh = ob.data
    if not mesh.vertices:
        return True
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)[::max(1, len(mesh.vertices) // 500_000)]  # a sample is plenty on dense meshes
    region = context.region
    project = np.array(context.region_data.perspective_matrix @ ob.matrix_world, dtype=np.float32)
    w = co @ project[3, :3] + project[3, 3]
    in_front = w > 1e-6
    w = np.where(in_front, w, 1.0)
    x = ((co @ project[0, :3] + project[0, 3]) / w * 0.5 + 0.5) * region.width
    y = ((co @ project[1, :3] + project[1, 3]) / w * 0.5 + 0.5) * region.height
    return not bool((in_front & (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)).any())


def _has_mask(ob):
    """Whether anything is masked. A Multires mask is stored out of reach, so that counts as masked."""
    if any(m.type == 'MULTIRES' for m in ob.modifiers):
        return True
    attribute = ob.data.attributes.get(".sculpt_mask")
    if attribute is None:
        return False
    values = np.empty(len(attribute.data), dtype=np.float32)
    attribute.data.foreach_get("value", values)
    return bool((values > 0.0).any())


def _canvas_drag_remeshes():
    prefs = common.prefs()
    return (prefs is not None and prefs.use_remesh and prefs.remesh_canvas_gesture
            and hasattr(bpy.types, "SCULPTKIT_OT_voxel_remesh"))


class SCULPTKIT_OT_canvas_mask(bpy.types.Operator):
    """Mask gestures that start on empty canvas: click inverts, drag box-masks, a box that misses the model clears"""
    bl_idname = "sculptkit.canvas_mask"
    bl_label = "Canvas Mask Gesture"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    unmask: BoolProperty(name="Unmask", default=False)

    CLICK_TOLERANCE = 6  # pixels the cursor may wander and still count as a click

    @classmethod
    def poll(cls, context):
        return common.brush_tool_active(context) and context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        self._start = self._end = (event.mouse_region_x, event.mouse_region_y)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _draw(self):
        import gpu
        from gpu_extras.batch import batch_for_shader
        (x0, y0), (x1, y1) = self._start, self._end
        if (x0, y0) == (x1, y1):
            return
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]})
        gpu.state.blend_set('ALPHA')
        shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9))
        batch.draw(shader)
        gpu.state.blend_set('NONE')

    def _finish(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        context.area.tag_redraw()

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            self._end = (event.mouse_region_x, event.mouse_region_y)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._finish(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._end = (event.mouse_region_x, event.mouse_region_y)
            self._finish(context)
            self._apply(context)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def _apply(self, context):
        (x0, y0), (x1, y1) = self._start, self._end
        xmin, xmax, ymin, ymax = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        if xmax - xmin <= self.CLICK_TOLERANCE and ymax - ymin <= self.CLICK_TOLERANCE:
            bpy.ops.paint.mask_flood_fill(mode='INVERT')
            return
        if _box_misses_model(context, xmin, xmax, ymin, ymax):
            if _canvas_drag_remeshes() and not _has_mask(context.active_object):
                bpy.ops.sculptkit.voxel_remesh()  # ZBrush's DynaMesh gesture
            else:
                bpy.ops.paint.mask_flood_fill(mode='VALUE', value=0.0)
        else:
            bpy.ops.paint.mask_box_gesture(xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax, wait_for_input=False,
                                           mode='VALUE', value=0.0 if self.unmask else 1.0)


class SCULPTKIT_OT_divide(bpy.types.Operator):
    """Add one subdivision level, creating a Multires modifier if the mesh has none"""
    bl_idname = "sculptkit.divide"
    bl_label = "Divide"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'

    def execute(self, context):
        ob = context.active_object
        multires = next((m for m in ob.modifiers if m.type == 'MULTIRES'), None)
        if multires is None:
            bpy.ops.object.modifier_add(type='MULTIRES')
            multires = next(m for m in ob.modifiers if m.type == 'MULTIRES')
        bpy.ops.object.multires_subdivide(modifier=multires.name, mode='CATMULL_CLARK')
        return {'FINISHED'}


_classes = (SCULPTKIT_OT_canvas_navigate, SCULPTKIT_OT_canvas_mask, SCULPTKIT_OT_divide)


# --------------------------------------------------------------------- keymap

def _radial(prop, unified_switch):
    """Properties for Blender's radial brush control, as its own F and Shift+F bindings set them."""
    brush = "tool_settings.sculpt.brush"
    unified = "tool_settings.sculpt.unified_paint_settings"
    return {
        "data_path_primary": brush + "." + prop,
        "data_path_secondary": unified + "." + prop,
        "use_secondary": unified + "." + unified_switch,
        "rotation_path": brush + ".texture_slot.angle",
        "color_path": brush + ".cursor_color_add",
        "image_id": brush,
    }


def _entries(prefs):
    """(operator, key, modifiers, properties) for each binding, in the order Blender should try them."""
    stroke = "sculpt.brush_stroke"
    canvas = prefs.keys_canvas
    skip_canvas = {"ignore_background_click": True}
    invert, smooth, mask = {"mode": 'INVERT'}, {"brush_toggle": 'SMOOTH'}, {"brush_toggle": 'MASK'}
    navigate = "sculptkit.canvas_navigate"
    rotate, move, zoom = {"action": 'ROTATE'}, {"action": 'MOVE'}, {"action": 'ZOOM'}
    entries = []

    if canvas:
        entries += [
            (stroke, 'LEFTMOUSE', "", skip_canvas),
            (navigate, 'LEFTMOUSE', "", rotate),
            (stroke, 'LEFTMOUSE', "shift", {**smooth, **skip_canvas}),
            (navigate, 'LEFTMOUSE', "shift", rotate),
        ]
    if prefs.keys_modifiers:
        # ZBrush: Alt inverts, Ctrl masks. Blender has these two the other way round.
        if canvas:
            entries += [
                (stroke, 'LEFTMOUSE', "alt", {**invert, **skip_canvas}),
                (navigate, 'LEFTMOUSE', "alt", move),
            ]
        else:
            entries += [(stroke, 'LEFTMOUSE', "alt", invert)]
        entries += [
            (stroke, 'LEFTMOUSE', "ctrl", {**mask, **skip_canvas}),
            ("sculptkit.canvas_mask", 'LEFTMOUSE', "ctrl", {"unmask": False}),
            (stroke, 'LEFTMOUSE', "ctrl alt", {**invert, **mask, **skip_canvas}),
            ("sculptkit.canvas_mask", 'LEFTMOUSE', "ctrl alt", {"unmask": True}),
        ]
    elif canvas:
        # Blender's own modifiers stay (Ctrl inverts, Alt masks), with navigation on the canvas.
        entries += [
            (stroke, 'LEFTMOUSE', "ctrl", {**invert, **skip_canvas}),
            (navigate, 'LEFTMOUSE', "ctrl", zoom),
            (stroke, 'LEFTMOUSE', "alt", {**mask, **skip_canvas}),
            (navigate, 'LEFTMOUSE', "alt", move),
        ]

    if prefs.keys_right_click:
        entries += [
            ("view3d.rotate", 'RIGHTMOUSE', "", {}),
            ("view3d.move", 'RIGHTMOUSE', "alt", {}),
            ("view3d.zoom", 'RIGHTMOUSE', "ctrl", {}),
            # Right-click no longer opens Blender's sculpt menu, so give it ZBrush's quick-menu key.
            ("wm.call_panel", 'SPACE', "", {"name": "VIEW3D_PT_sculpt_context_menu"}),
        ]

    if prefs.keys_hotkeys:
        entries += [
            ("wm.radial_control", 'S', "", _radial("size", "use_unified_size")),
            ("wm.radial_control", 'U', "", _radial("strength", "use_unified_strength")),
            ("view3d.view_all", 'F', "", {}),
            ("wm.context_toggle", 'F', "shift", {"data_path": "space_data.overlay.show_wireframes"}),
            ("view3d.view_persportho", 'P', "", {}),
            ("wm.context_toggle", 'X', "", {"data_path": "object.data.use_mirror_x"}),
            ("wm.call_asset_shelf_popover", 'B', "", {"name": "VIEW3D_AST_brush_sculpt"}),
            ("object.subdivision_set", 'D', "", {"level": 1, "relative": True, "ensure_modifier": False}),
            ("object.subdivision_set", 'D', "shift", {"level": -1, "relative": True, "ensure_modifier": False}),
            ("sculptkit.divide", 'D', "ctrl", {}),
        ]
    return entries


def _add_keymap(prefs):
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    keymap = keyconfig.keymaps.new(name="Sculpt", space_type='EMPTY', region_type='WINDOW')
    # Blender puts each add-on item in front of the ones before it, so add them last-first.
    for operator, key, modifiers, properties in reversed(_entries(prefs)):
        item = keymap.keymap_items.new(operator, key, 'PRESS', ctrl="ctrl" in modifiers,
                                       shift="shift" in modifiers, alt="alt" in modifiers)
        for name, value in properties.items():
            setattr(item.properties, name, value)
        _added.append((keymap, item))


def _remove_keymap():
    for keymap, item in _added:
        try:
            keymap.keymap_items.remove(item)
        except (RuntimeError, ReferenceError):
            pass
    _added.clear()


def rebuild():
    _remove_keymap()
    prefs = common.prefs()
    if prefs is not None:
        _add_keymap(prefs)


def enable():
    for cls in _classes:
        bpy.utils.register_class(cls)
    rebuild()


def disable():
    _remove_keymap()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
