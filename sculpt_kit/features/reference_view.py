"""One-click synced reference view.

Splits the viewport in two: the reference on the left, the sculpt you are
working on on the right. Local collections give each half its own contents.
The right shows everything except the reference collection, the left shows
only that.

The halves are kept on the same view by copying it across whenever either one
changes: where it looks from, perspective or orthographic, an axis view, zoom.
Whatever is done to the view in one half, the other follows. The focal length
follows the sculpt half, adjusted for the sizes of the two halves so that the
model is drawn at the same scale in both.

Versions before 0.3.1 had the halves share a hidden camera with Lock Camera to
View. That fell out of step as soon as one half left camera view, which is what
switching perspective off does. A file saved with such a view open is tidied
up the first time the button is pressed in it.
"""
import bpy
from bpy.props import PointerProperty

from .. import common

MARK = "sculpt_kit_reference_view"             # on a screen while its reference view is open
LEGACY_CAMERA = "sculpt_kit_reference_camera"  # on the hidden camera that earlier versions looked through
REFERENCE_NAMES = ("Reference", "References", "Ref", "reference", "references", "ref")
TOLERANCE = 1e-5

_halves_of = {}  # screen pointer -> (reference area pointer, sculpt area pointer)
_shown = {}      # screen pointer -> the view both halves were last brought to
_handle = None


def _view3d_areas(screen):
    return [a for a in screen.areas if a.type == 'VIEW_3D']


# ------------------------------------------------------------------ the halves

def _halves(screen):
    """(reference area, sculpt area) of the screen's reference view, or None."""
    areas = {a.as_pointer(): a for a in _view3d_areas(screen)}
    known = _halves_of.get(screen.as_pointer())
    if known and known[0] in areas and known[1] in areas:
        return areas[known[0]], areas[known[1]]
    # After a file is loaded the areas are new ones: the halves are the two viewports with collections of their own.
    own = sorted((a for a in areas.values() if a.spaces.active.use_local_collections), key=lambda a: a.x)
    if len(own) != 2:
        return None
    _halves_of[screen.as_pointer()] = (own[0].as_pointer(), own[1].as_pointer())
    return own[0], own[1]


def is_open(screen):
    return bool(_legacy_areas(screen)) or (bool(screen.get(MARK)) and _halves(screen) is not None)


def _forget(screen):
    if MARK in screen:
        del screen[MARK]
    _halves_of.pop(screen.as_pointer(), None)
    _shown.pop(screen.as_pointer(), None)


# -------------------------------------------------------- keeping them in step

def _plain(value):
    return value if isinstance(value, (bool, int, float, str)) else tuple(value)


def _view(space):
    r = space.region_3d
    return (r.view_perspective, r.is_orthographic_side_view, _plain(r.view_location), _plain(r.view_rotation),
            r.view_distance, r.view_camera_zoom, _plain(r.view_camera_offset), space.clip_start, space.clip_end)


def _close_to(a, b):
    if isinstance(a, tuple):
        apart = max(abs(x - y) for x, y in zip(a, b))
        if len(a) == 4:  # a rotation: q and -q are the same turn
            apart = min(apart, max(abs(x + y) for x, y in zip(a, b)))
        return apart <= TOLERANCE * max(1.0, max(abs(x) for x in a))
    if isinstance(a, float):
        return abs(a - b) <= TOLERANCE * max(1.0, abs(a))
    return a == b


def _same(view_a, view_b):
    return all(_close_to(a, b) for a, b in zip(view_a, view_b))


def _show(space, view):
    """Bring a viewport to a view, touching only what differs."""
    r = space.region_3d
    perspective, side_view, location, rotation, distance, camera_zoom, camera_offset, clip_start, clip_end = view
    for owner, name, value in ((r, "view_perspective", perspective), (r, "view_location", location),
                               (r, "view_rotation", rotation), (r, "view_distance", distance),
                               (r, "view_camera_zoom", camera_zoom), (r, "view_camera_offset", camera_offset),
                               (space, "clip_start", clip_start), (space, "clip_end", clip_end)):
        if not _close_to(_plain(getattr(owner, name)), value):
            setattr(owner, name, value)
    if r.is_orthographic_side_view != side_view:  # after the rotation, which is what makes it an axis view
        r.is_orthographic_side_view = side_view


def _fit(area):
    """The side of a viewport that its focal length is measured across."""
    region = common.window_region(area)
    return max(region.width, region.height, 1)


def _match_scale(reference_area, sculpt_area):
    """Draw the model the same size in both halves, even when one is wider than the other.

    A viewport's focal length spans its longer side, so the same view in a
    wider viewport shows the model larger. The reference half takes the sculpt
    half's focal length scaled by their sizes instead: same position, same
    perspective, same scale, only more or less of the surroundings."""
    lens = sculpt_area.spaces.active.lens * _fit(sculpt_area) / _fit(reference_area)
    lens = min(max(lens, 1.0), 250.0)
    space = reference_area.spaces.active
    if abs(space.lens - lens) > 0.01:
        space.lens = lens
        reference_area.tag_redraw()


def _keep_in_step():
    """Runs as each 3D viewport is drawn. If this one is half of a reference view and its view has
    changed since both halves were last brought together, the other half is brought to it."""
    context = bpy.context
    screen, area = context.screen, context.area
    if screen is None or area is None or not screen.get(MARK):
        return
    halves = _halves(screen)
    if halves is None or area not in halves:
        return
    reference_area, sculpt_area = halves
    _match_scale(reference_area, sculpt_area)
    key = screen.as_pointer()
    view = _view(area.spaces.active)
    if key in _shown and _same(view, _shown[key]):
        return
    _shown[key] = view
    other = reference_area if area == sculpt_area else sculpt_area
    if not _same(_view(other.spaces.active), view):
        _show(other.spaces.active, view)
        other.tag_redraw()


# ------------------------------------------------------------ opening, closing

def _layer_collection_index(view_layer, collection):
    """The index `object.hide_collection` expects: depth-first position under the scene collection, from 1."""
    counter = 0

    def walk(layer_collection):
        nonlocal counter
        for child in layer_collection.children:
            counter += 1
            if child.collection == collection:
                return counter
            found = walk(child)
            if found:
                return found
        return 0

    return walk(view_layer.layer_collection)


def _reference_collection(scene, report):
    collection = scene.sculpt_kit_reference
    if collection is None:
        collection = next((c for c in scene.collection.children_recursive if c.name in REFERENCE_NAMES), None)
    if collection is None:
        collection = bpy.data.collections.new("Reference")
        scene.collection.children.link(collection)
        report({'INFO'}, "Created an empty Reference collection: put your reference objects in it")
    scene.sculpt_kit_reference = collection
    return collection


def _open(context, report):
    scene, screen, area = context.scene, context.screen, context.area

    reference = _reference_collection(scene, report)
    context.view_layer.update()
    index = _layer_collection_index(context.view_layer, reference)
    if not index:
        report({'ERROR'}, "The reference collection is not part of this scene")
        return {'CANCELLED'}

    before = {a.as_pointer() for a in screen.areas}
    with context.temp_override(area=area, region=common.window_region(area)):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)
    other = next(a for a in screen.areas if a.as_pointer() not in before)
    reference_area, sculpt_area = sorted((area, other), key=lambda a: a.x)

    for view_area, isolate in ((sculpt_area, False), (reference_area, True)):
        space = view_area.spaces.active
        space.use_local_collections = False  # so that it starts from what the scene shows
        space.use_local_collections = True
        with context.temp_override(area=view_area, region=common.window_region(view_area)):
            if isolate:
                bpy.ops.object.hide_collection(collection_index=index)
            else:
                bpy.ops.object.hide_collection(collection_index=index, toggle=True)

    # The reference half is for looking at: nothing drawn over it, no panels down its sides.
    reference_space = reference_area.spaces.active
    reference_space.overlay.show_overlays = False
    reference_space.show_gizmo = False
    reference_space.show_region_ui = False
    reference_space.show_region_toolbar = False
    try:
        reference_space.show_region_asset_shelf = False
    except AttributeError:
        pass  # read-only outside the modes that have a brush shelf

    screen[MARK] = True
    _halves_of[screen.as_pointer()] = (reference_area.as_pointer(), sculpt_area.as_pointer())
    _shown[screen.as_pointer()] = _view(sculpt_area.spaces.active)
    return {'FINISHED'}


def _close(context):
    screen = context.screen
    reference_area, sculpt_area = _halves(screen)
    with context.temp_override(area=reference_area, region=common.window_region(reference_area)):
        bpy.ops.screen.area_close()
    sculpt_area.spaces.active.use_local_collections = False
    _forget(screen)
    return {'FINISHED'}


# ------------------------------------------- views left open by earlier versions

def _legacy_camera(space):
    camera = space.camera if space.use_local_camera else None
    return camera if camera is not None and camera.get(LEGACY_CAMERA) else None


def _legacy_areas(screen):
    return sorted((a for a in _view3d_areas(screen) if _legacy_camera(a.spaces.active)), key=lambda a: a.x)


def _close_legacy(context):
    """Close a reference view from a version that used a hidden camera: sculpt on the left, reference on the right."""
    screen = context.screen  # read now: it is gone from the context once an area has been closed
    sculpt_area, *others = _legacy_areas(screen)
    space = sculpt_area.spaces.active
    rv3d = space.region_3d
    camera = _legacy_camera(space)
    view = camera.matrix_world.inverted()
    orthographic = camera.data.type == 'ORTHO'
    ortho_distance = camera.data.ortho_scale * space.lens / 288.0  # how those versions sized the camera's frame

    for area in others:
        with context.temp_override(area=area, region=common.window_region(area)):
            bpy.ops.screen.area_close()
    space.lock_camera = False
    rv3d.view_perspective = 'ORTHO' if orthographic else 'PERSP'
    if orthographic:
        rv3d.view_distance = ortho_distance
    rv3d.view_matrix = view
    space.use_local_camera = False
    space.use_local_collections = False

    data = camera.data
    bpy.data.objects.remove(camera)
    bpy.data.cameras.remove(data)
    _forget(screen)
    return {'FINISHED'}


# -------------------------------------------------------------------- interface

class SCULPTKIT_OT_reference_view(bpy.types.Operator):
    """Split the viewport: the reference on the left, the sculpt on the right, both kept on the same view"""
    bl_idname = "sculptkit.reference_view"
    bl_label = "Synced Reference View"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        screen = context.screen
        if _legacy_areas(screen):
            return _close_legacy(context)
        if is_open(screen):
            return _close(context)
        return _open(context, self.report)


class SCULPTKIT_PT_reference_view(bpy.types.Panel):
    bl_label = "Reference View"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = common.PANEL_CATEGORY
    bl_order = 1

    def draw(self, context):
        col = self.layout.column()
        opened = is_open(context.screen)
        sub = col.column()
        sub.enabled = not opened
        sub.prop(context.scene, "sculpt_kit_reference", text="")
        col.operator(SCULPTKIT_OT_reference_view.bl_idname, depress=opened, icon='WINDOW',
                     text="Close Reference View" if opened else "Open Reference View")


_classes = (SCULPTKIT_OT_reference_view, SCULPTKIT_PT_reference_view)


def enable():
    global _handle
    bpy.types.Scene.sculpt_kit_reference = PointerProperty(
        type=bpy.types.Collection, name="Reference Collection",
        description="What the reference view shows. Left empty, a collection named Reference is used or created")
    for cls in _classes:
        bpy.utils.register_class(cls)
    _handle = bpy.types.SpaceView3D.draw_handler_add(_keep_in_step, (), 'WINDOW', 'PRE_VIEW')


def disable():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.sculpt_kit_reference
