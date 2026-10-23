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

The reference half's header, tool header and brush shelf stay, so that its
canvas is the same height as the sculpt's, but they draw nothing: the only
controls on screen are the sculpt half's.

While the view is open, new objects go to the sculpt's side: Blender adds
them to the active collection, so whenever that is the reference collection
(clicking a reference in the Outliner makes it so), the collection of the
mesh being worked on, or else the scene's own, takes its place.

The halves are also kept to the same frame, so that the edge of the picture
can be measured against like a pair of calipers: bring the reference up to an
edge, then sculpt up to the same edge. The view opens with both canvases the
same size: the same width, and the same height, for which the reference half
keeps a brush shelf under it wherever the sculpt's shelf takes room from its
canvas. The two pictures are then level across the divider as well. Wherever
one half still shows more than the other (a panel lies over part of a canvas,
the divider was dragged), the extra is dimmed, and the edge of what is left
bright is the same place in both.

Versions before 0.3.1 had the halves share a hidden camera with Lock Camera to
View. That fell out of step as soon as one half left camera view, which is what
switching perspective off does. A file saved with such a view open is tidied
up the first time the button is pressed in it.
"""
import bpy
import gpu
from bpy.props import BoolProperty, PointerProperty
from gpu_extras.batch import batch_for_shader

from .. import common

MARK = "sculpt_kit_reference_view"             # on a screen while its reference view is open
LEGACY_CAMERA = "sculpt_kit_reference_camera"  # on the hidden camera that earlier versions looked through
REFERENCE_NAMES = ("Reference", "References", "Ref", "reference", "references", "ref")
TOLERANCE = 1e-5
SLIVER = 3                      # pixels: a difference between the halves this small is not marked
DIM = (0.0, 0.0, 0.0, 0.4)      # over what one half shows and the other does not
EDGE = (1.0, 1.0, 1.0, 0.35)    # the line where that begins

_halves_of = {}  # screen pointer -> (reference area pointer, sculpt area pointer)
_blanked = []    # (class, attribute, what the class itself had there or None) while the reference's bars are blank
_shown = {}      # screen pointer -> the view both halves were last brought to
_matcaps = {}    # screen pointer -> the matcap both halves were last given, or None while either is not in one
_handle = None
_frame_handle = None


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
    _matcaps.pop(screen.as_pointer(), None)


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
    if _adds_to_reference(context.view_layer, context.scene) and not bpy.app.timers.is_registered(_add_to_sculpt):
        bpy.app.timers.register(_add_to_sculpt, first_interval=0.0)  # not while drawing
    if _bars_differ(reference_area, sculpt_area) and not bpy.app.timers.is_registered(_match_bars):
        bpy.app.timers.register(_match_bars, first_interval=0.0)
    key = screen.as_pointer()
    if _matcaps_differ(reference_area, sculpt_area) and not bpy.app.timers.is_registered(_match_matcaps):
        bpy.app.timers.register(_match_matcaps, first_interval=0.0)  # not while drawing
    view = _view(area.spaces.active)
    if key in _shown and _same(view, _shown[key]):
        return
    _shown[key] = view
    other = reference_area if area == sculpt_area else sculpt_area
    if not _same(_view(other.spaces.active), view):
        _show(other.spaces.active, view)
        other.tag_redraw()


BARS = ("show_region_header", "show_region_tool_header")


def _bars_differ(reference_area, sculpt_area):
    """Whether the reference half shows a different set of bars across its top than the sculpt half: its canvas
    would then be a different height, and the two pictures would not be level."""
    reference, sculpt = reference_area.spaces.active, sculpt_area.spaces.active
    return any(getattr(reference, bar) != getattr(sculpt, bar) for bar in BARS)


def _matcap(area):
    shading = area.spaces.active.shading
    return shading.studio_light if shading.light == 'MATCAP' else None


def _matcaps_differ(reference_area, sculpt_area):
    reference, sculpt = _matcap(reference_area), _matcap(sculpt_area)
    return reference is not None and sculpt is not None and reference != sculpt


def _match_matcaps():
    """Give both halves of every open reference view the matcap that was just picked in either of them. The one
    that no longer matches what both had is the one that changed; when there is nothing to go by, the sculpt's."""
    for window in bpy.context.window_manager.windows:
        halves = _halves(window.screen) if window.screen.get(MARK) else None
        if halves is None or not _matcaps_differ(*halves):
            continue
        reference, sculpt = (_matcap(area) for area in halves)
        before = _matcaps.get(window.screen.as_pointer())
        picked = reference if sculpt == before else sculpt
        for area in halves:
            area.spaces.active.shading.studio_light = picked
            area.tag_redraw()
        _matcaps[window.screen.as_pointer()] = picked


def _match_bars():
    """Give the reference half of every open reference view the sculpt half's bars."""
    for window in bpy.context.window_manager.windows:
        halves = _halves(window.screen) if window.screen.get(MARK) else None
        if halves is not None and _bars_differ(*halves):
            reference, sculpt = (area.spaces.active for area in halves)
            for bar in BARS:
                setattr(reference, bar, getattr(sculpt, bar))


def _reference_branch(scene):
    reference = scene.sculpt_kit_reference
    return set() if reference is None else {reference, *reference.children_recursive}


def _adds_to_reference(view_layer, scene):
    return view_layer.active_layer_collection.collection in _reference_branch(scene)


def _add_to_sculpt():
    """Make new objects go to the sculpt's side of every open reference view."""
    for window in bpy.context.window_manager.windows:
        view_layer, scene = window.view_layer, window.scene
        if not window.screen.get(MARK) or not _adds_to_reference(view_layer, scene):
            continue
        reference = _reference_branch(scene)
        active = view_layer.objects.active
        wanted = next((c for c in (active.users_collection if active else ()) if c not in reference), None)
        layers = [view_layer.layer_collection]
        while layers:
            layer = layers.pop()
            if layer.collection == wanted:
                break
            layers.extend(layer.children)
        else:
            layer = view_layer.layer_collection
        view_layer.active_layer_collection = layer


def _in_reference_half(context):
    screen, area = context.screen, context.area
    if screen is None or area is None or not screen.get(MARK):
        return False
    halves = _halves(screen)
    return halves is not None and area == halves[0]


def _blank_bars():
    """Make the reference half's header, tool header and brush shelf draw nothing. Blender looks these methods up
    by name each time, so wrapping them on the class takes effect at once, and unwrapping undoes it."""
    def blank_draw(cls):
        functions = cls._dyn_ui_initialize()  # the list add-ons append to and swap in; it must stay the same list
        drawn = cls.draw

        def draw(self, context):
            if not _in_reference_half(context):
                drawn(self, context)
        draw._draw_funcs = functions
        return draw

    def blank_assets(cls):
        allowed = cls.asset_poll

        def asset_poll(cls_, asset):
            return not _in_reference_half(bpy.context) and allowed(asset)
        return classmethod(asset_poll)

    for cls, name, wrap in ((bpy.types.VIEW3D_HT_header, "draw", blank_draw),
                            (bpy.types.VIEW3D_HT_tool_header, "draw", blank_draw),
                            (bpy.types.VIEW3D_AST_brush_sculpt, "asset_poll", blank_assets)):
        _blanked.append((cls, name, cls.__dict__.get(name)))
        setattr(cls, name, wrap(cls))


def _unblank_bars():
    for cls, name, own in reversed(_blanked):
        if own is None:
            delattr(cls, name)
        else:
            setattr(cls, name, own)
    _blanked.clear()


# ------------------------------------------------------------ the same frame

# Regions that can be seen through when they float over the canvas: what is under them still counts as shown.
# The brush shelf is see-through too, but the halves are compared at the border along its top, as Jacob asked.
# The toolbar is not see-through: its left edge must be matched on the reference side.
SEE_THROUGH = {'HUD'}


def _clear(area, blank=False):
    """The part of a viewport's canvas that no panel hides, as (left, bottom, right, top) in pixels
    from the middle of the canvas, which is where both halves show the same point. `blank` is for the reference
    half, whose bars draw nothing: where they float over the canvas, it shows through them."""
    canvas = common.window_region(area)
    left, bottom, right, top = 0, 0, canvas.width, canvas.height
    see_through_bars = bpy.context.preferences.themes[0].view_3d.space.header[3] < 1.0  # an empty one hides nothing
    for region in area.regions:
        if region.type == 'WINDOW' or region.width <= 1 or region.height <= 1:  # hidden ones are a pixel wide
            continue
        if region.type in SEE_THROUGH or (blank and region.type in {'HEADER', 'TOOL_HEADER'} and see_through_bars):
            continue  # beside the canvas it is not over it; over it, the canvas shows through
        if (region.x >= canvas.x + canvas.width or region.x + region.width <= canvas.x
                or region.y >= canvas.y + canvas.height or region.y + region.height <= canvas.y):
            continue  # beside the canvas, not over it
        if region.alignment == 'LEFT':
            left = max(left, region.x + region.width - canvas.x)
        elif region.alignment == 'RIGHT':
            right = min(right, region.x - canvas.x)
        elif region.alignment == 'BOTTOM':
            bottom = max(bottom, region.y + region.height - canvas.y)
        elif region.alignment == 'TOP':
            top = min(top, region.y - canvas.y)
    x, y = canvas.width / 2, canvas.height / 2
    return left - x, bottom - y, right - x, top - y


def shared_frame(screen):
    """What both halves show, as (left, bottom, right, top) in pixels from the middle of either canvas. None if closed."""
    halves = _halves(screen) if screen.get(MARK) else None
    if halves is None:
        return None
    a, b = _clear(halves[0], blank=True), _clear(halves[1])
    return max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])


def bands(screen, area):
    """The strips of a half's canvas that the other half does not show, as (x0, y0, x1, y1) in canvas pixels."""
    frame = shared_frame(screen)
    if frame is None or area not in _halves(screen) or area.spaces.active.region_3d.view_perspective == 'CAMERA':
        return []
    canvas = common.window_region(area)
    width, height, x, y = canvas.width, canvas.height, canvas.width / 2, canvas.height / 2
    own = _clear(area, blank=area == _halves(screen)[0])
    x0 = x + frame[0] if frame[0] - own[0] > SLIVER else 0
    x1 = x + frame[2] if own[2] - frame[2] > SLIVER else width
    strips = []
    if x0 > 0:
        strips.append((0, 0, x0, height))
    if x1 < width:
        strips.append((x1, 0, width, height))
    if frame[1] - own[1] > SLIVER:
        strips.append((x0, 0, x1, y + frame[1]))
    if own[3] - frame[3] > SLIVER:
        strips.append((x0, y + frame[3], x1, height))
    return strips


def _draw_frame():
    """Runs over each 3D viewport once it is drawn: dims what this half shows and the other does not."""
    context = bpy.context
    screen, area = context.screen, context.area
    if screen is None or area is None or not screen.get(MARK) or not context.scene.sculpt_kit_reference_frame:
        return
    strips = bands(screen, area)
    if not strips:
        return
    canvas = common.window_region(area)
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    shader.uniform_float("color", DIM)
    corners = [corner for x0, y0, x1, y1 in strips for corner in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    faces = [(i, i + 1, i + 2) for i in range(0, len(corners), 4)] + [(i, i + 2, i + 3) for i in range(0, len(corners), 4)]
    batch_for_shader(shader, 'TRIS', {"pos": corners}, indices=faces).draw(shader)
    ends = []
    for x0, y0, x1, y1 in strips:  # along the side of each strip that faces the middle
        if y0 == 0 and y1 == canvas.height:
            ends += [(x1, y0), (x1, y1)] if x0 == 0 else [(x0, y0), (x0, y1)]
        else:
            ends += [(x0, y1), (x1, y1)] if y0 == 0 else [(x0, y0), (x1, y0)]
    shader.uniform_float("color", EDGE)
    batch_for_shader(shader, 'LINES', {"pos": ends}).draw(shader)
    gpu.state.blend_set('NONE')


# ------------------------------------------------------------ opening, closing

def _layer_collections(view_layer):
    """Every collection of the view layer as (index, layer collection, the one it is inside). The index is the
    one `object.hide_collection` knows it by: counting from 1, a collection's own children all come before any
    of their children."""
    numbered = []

    def walk(parent):
        children = list(parent.children)
        for child in children:
            numbered.append((len(numbered) + 1, child, parent))
        for child in children:
            walk(child)

    walk(view_layer.layer_collection)
    return numbered


def _branch(numbered, index):
    """The indices of a collection, of the ones it is inside and of the ones inside it."""
    index_of = {layer.as_pointer(): i for i, layer, _parent in numbered}
    parent_of = {layer.as_pointer(): parent for _i, layer, parent in numbered}
    target = next(layer for i, layer, _parent in numbered if i == index)
    branch = {index}
    parent = parent_of[target.as_pointer()]
    while parent.as_pointer() in index_of:
        branch.add(index_of[parent.as_pointer()])
        parent = parent_of[parent.as_pointer()]
    inside = [target]
    while inside:
        layer = inside.pop()
        for i, child, parent in numbered:
            if parent == layer:
                branch.add(i)
                inside.append(child)
    return branch


def _show_collections(context, area, numbered, index, alone):
    """Give a viewport collections of its own: the one at `index` alone, or everything but it.

    Blender offers "show this alone" and "flip this one", and no way to read what a viewport shows. What it
    starts from can be left over from the last time local collections were on, so nothing is assumed: the
    collection is shown alone, which hides every other; then, for everything but it, each of those is flipped
    back on and it is flipped off."""
    area.spaces.active.use_local_collections = True
    with context.temp_override(area=area, region=common.window_region(area)):
        bpy.ops.object.hide_collection(collection_index=index)
        if not alone:
            for other in sorted({i for i, _layer, _parent in numbered} - _branch(numbered, index)):
                bpy.ops.object.hide_collection(collection_index=other, toggle=True)
            bpy.ops.object.hide_collection(collection_index=index, toggle=True)


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
    numbered = _layer_collections(context.view_layer)
    index = next((i for i, layer, _parent in numbered if layer.collection == reference), 0)
    if not index:
        report({'ERROR'}, "The reference collection is not part of this scene")
        return {'CANCELLED'}

    # Split so that both canvases come out the same width: the panels that sit beside the sculpt's canvas
    # stay with the sculpt half, and the reference half has none.
    beside = area.width - common.window_region(area).width
    factor = min(0.5, max(0.2, (area.width - beside) / (2.0 * area.width)))
    before = {a.as_pointer() for a in screen.areas}
    with context.temp_override(area=area, region=common.window_region(area)):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=factor)
    # Up to a half, Blender puts the new area on the left. Where the areas are cannot be read until the
    # screen has been drawn again: straight after a split they both still report where they used to be.
    reference_area = next(a for a in screen.areas if a.as_pointer() not in before)
    sculpt_area = area

    _show_collections(context, sculpt_area, numbered, index, alone=False)
    _show_collections(context, reference_area, numbered, index, alone=True)

    # The reference half is for looking at: nothing drawn over it, no panels down its sides.
    reference_space = reference_area.spaces.active
    reference_space.overlay.show_overlays = False
    reference_space.show_gizmo = False
    reference_space.show_region_ui = False
    reference_space.show_region_toolbar = False
    # Under it, a brush shelf like the sculpt's wherever shelves take room from the canvas: the canvases are
    # then the same height, so the same point is at the same height on screen in both halves. A view cannot
    # be slid up or down inside its canvas instead without changing where it looks from. Shelves that float
    # over the canvas take no room, and there the reference does without one.
    beside = not context.preferences.system.use_region_overlap
    try:
        reference_space.show_region_asset_shelf = beside and sculpt_area.spaces.active.show_region_asset_shelf
    except AttributeError:
        pass  # read-only outside the modes that have a brush shelf

    screen[MARK] = True
    _halves_of[screen.as_pointer()] = (reference_area.as_pointer(), sculpt_area.as_pointer())
    _shown[screen.as_pointer()] = _view(sculpt_area.spaces.active)
    _matcaps[screen.as_pointer()] = _matcap(sculpt_area)
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
    bl_order = 2

    def draw(self, context):
        col = self.layout.column()
        opened = is_open(context.screen)
        sub = col.column()
        sub.enabled = not opened
        sub.prop(context.scene, "sculpt_kit_reference", text="")
        col.operator(SCULPTKIT_OT_reference_view.bl_idname, depress=opened, icon='WINDOW',
                     text="Close Reference View" if opened else "Open Reference View")
        col.prop(context.scene, "sculpt_kit_reference_frame")


_classes = (SCULPTKIT_OT_reference_view, SCULPTKIT_PT_reference_view)


def enable():
    global _handle, _frame_handle
    bpy.types.Scene.sculpt_kit_reference = PointerProperty(
        type=bpy.types.Collection, name="Reference Collection",
        description="What the reference view shows. Left empty, a collection named Reference is used or created")
    bpy.types.Scene.sculpt_kit_reference_frame = BoolProperty(
        name="Matching Edges", default=True, update=lambda _self, _context: common.redraw_all(),
        description="Dim what one half shows and the other does not. The edge of what stays bright is then the "
                    "same place in both: bring the reference up to it, then sculpt up to it")
    for cls in _classes:
        bpy.utils.register_class(cls)
    _blank_bars()
    _handle = bpy.types.SpaceView3D.draw_handler_add(_keep_in_step, (), 'WINDOW', 'PRE_VIEW')
    _frame_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_frame, (), 'WINDOW', 'POST_PIXEL')


def disable():
    global _handle, _frame_handle
    for handle in (_handle, _frame_handle):
        if handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handle = _frame_handle = None
    _unblank_bars()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.sculpt_kit_reference_frame
    del bpy.types.Scene.sculpt_kit_reference
