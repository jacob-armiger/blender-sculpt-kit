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

The shading is kept the same too: everything in the Shading popover, from
Solid, Material Preview or Rendered and the lighting (Flat, Studio or Matcap,
which one, its rotation, strength and blur) to background, cavity, shadows
and X-ray. Whichever half a setting is changed in, the other follows. Each
half keeps its own Color section and its own Matcap Flip (which Blender does not
let add-ons read). The reference half starts as a copy of the sculpt's, so where
the sculpt is shown by Attribute or Texture colour the reference starts in
Material instead: a scanned reference carries colour layers of its own (Cast
Hall's masks show purple and yellow), and its bars draw nothing to change it by.

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
"""
import functools
import math

import bpy
import gpu
from bpy.props import BoolProperty, PointerProperty
from gpu_extras.batch import batch_for_shader

from .. import common

MARK = "sculpt_kit_reference_view"             # on a screen while its reference view is open
REFERENCE_NAMES = ("Reference", "References", "Ref", "reference", "references", "ref")
TOLERANCE = 1e-5
SLIVER = 3                      # pixels: a difference between the halves this small is not marked
DIM = (0.0, 0.0, 0.0, 0.4)      # over what one half shows and the other does not
EDGE = (1.0, 1.0, 1.0, 0.35)    # the line where that begins

_halves_of = {}  # screen pointer -> (reference area pointer, sculpt area pointer)
_blanked = []    # (class, attribute, what the class itself had there or None, ours) while the reference's bars are blank
_blanking = False  # off: a wrapper of ours that another add-on wrapped in turn (so it stays) just passes through
_shown = {}      # screen pointer -> the view both halves were last brought to
_shadings = {}   # screen pointer -> the shading both halves were last given
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
    return bool(screen.get(MARK)) and _halves(screen) is not None


def _forget(screen):
    if MARK in screen:
        del screen[MARK]
    _halves_of.pop(screen.as_pointer(), None)
    _shown.pop(screen.as_pointer(), None)
    _shadings.pop(screen.as_pointer(), None)


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
    if halves is None:  # one half was closed with Blender's own Close Area
        if not bpy.app.timers.is_registered(_tidy_broken):
            bpy.app.timers.register(_tidy_broken, first_interval=0.0)  # not while drawing
        return
    if area not in halves:
        return
    reference_area, sculpt_area = halves
    _match_scale(reference_area, sculpt_area)
    if _adds_to_reference(context.view_layer, context.scene) and not bpy.app.timers.is_registered(_add_to_sculpt):
        bpy.app.timers.register(_add_to_sculpt, first_interval=0.0)  # not while drawing
    if _bars_differ(reference_area, sculpt_area) and not bpy.app.timers.is_registered(_match_bars):
        bpy.app.timers.register(_match_bars, first_interval=0.0)
    key = screen.as_pointer()
    if _shadings_differ(reference_area, sculpt_area) and not bpy.app.timers.is_registered(_match_shading):
        bpy.app.timers.register(_match_shading, first_interval=0.0)  # not while drawing
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


# Set first, in this order: which studio lights and background choices there are depends on them.
_SHADING_FIRST = ("type", "light", "background_type", "studio_light")
# The Color section stays each half's own: a reference coloured by the sculpt's choice shows its own colour
# attributes or materials (a sculpt in Attribute colour turned a scanned reference purple and yellow).
_SHADING_OWN = {"color_type", "single_color", "wireframe_color_type"}


@functools.cache
def _shading_settings():
    """Every setting in the viewport's Shading popover that a script can set, but the Color section's, the ones
    others depend on first."""
    rest = [p.identifier for p in bpy.types.View3DShading.bl_rna.properties
            if not p.is_readonly and p.type not in {'POINTER', 'COLLECTION'}
            and p.identifier not in _SHADING_FIRST and p.identifier not in _SHADING_OWN]
    return _SHADING_FIRST + tuple(rest)


def _shading(area):
    """How a viewport shades the model, as a tuple of _shading_settings()."""
    shading = area.spaces.active.shading
    return tuple(_plain(getattr(shading, name)) for name in _shading_settings())


def _shadings_differ(reference_area, sculpt_area):
    return not _same(_shading(reference_area), _shading(sculpt_area))


def _shade(area, values):
    """Give a viewport these shading settings, touching only what differs."""
    shading = area.spaces.active.shading
    for name, value in zip(_shading_settings(), values):
        if not _close_to(_plain(getattr(shading, name)), value):
            try:
                setattr(shading, name, value)
            except (TypeError, ValueError):
                pass  # a choice the other half's settings do not offer here


def _match_shading():
    """Give both halves of every open reference view the shading that was just set in either of them: the
    lighting (Flat, Studio, Matcap and which one, its rotation, strength and blur), background, cavity, shadows,
    X-ray and the rest of the Shading popover but its Color section, and Solid, Material Preview or Rendered. Setting by
    setting, the half that no longer matches what both had is the one that changed; when there is nothing to
    go by, the sculpt's."""
    for window in bpy.context.window_manager.windows:
        halves = _halves(window.screen) if window.screen.get(MARK) else None
        if halves is None or not _shadings_differ(*halves):
            continue
        reference, sculpt = (_shading(area) for area in halves)
        before = _shadings.get(window.screen.as_pointer())
        picked = tuple(r if _close_to(r, s) or (before is not None and _close_to(s, b)) else s
                       for r, s, b in zip(reference, sculpt, before or sculpt))
        for area in halves:
            _shade(area, picked)
            area.tag_redraw()
        _shadings[window.screen.as_pointer()] = _shading(halves[1])


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
            if not (_blanking and _in_reference_half(context)):
                drawn(self, context)
        draw._draw_funcs = functions
        return draw

    def blank_assets(cls):
        allowed = cls.asset_poll

        def asset_poll(cls_, asset):
            return not (_blanking and _in_reference_half(bpy.context)) and allowed(asset)
        return classmethod(asset_poll)

    global _blanking
    _blanking = True
    for cls, name, wrap in ((bpy.types.VIEW3D_HT_header, "draw", blank_draw),
                            (bpy.types.VIEW3D_HT_tool_header, "draw", blank_draw),
                            (bpy.types.VIEW3D_AST_brush_sculpt, "asset_poll", blank_assets)):
        ours = wrap(cls)
        _blanked.append((cls, name, cls.__dict__.get(name), ours))
        setattr(cls, name, ours)


def _unblank_bars():
    """Put back what the classes had, where ours is still what they hold. Where another add-on has wrapped ours
    since, putting back would throw its wrapper away: ours stays in its chain and passes straight through."""
    global _blanking
    _blanking = False
    for cls, name, own, ours in reversed(_blanked):
        if cls.__dict__.get(name) is not ours:
            continue
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
# A sidebar or toolbar floating over the canvas shows its own background where the theme gives it one (Blender's own
# gives none), else only the panels or buttons on it, which Blender draws this far in from the region's edge, in
# pixels per unit of interface scale (measured in Blender 5.2, at 1 and 1.5): the edge the eye sees.
INSET = {'UI': 7, 'TOOLS': 8}


def _inset(region):
    """How far in from a region's inner edge its visible edge is, in pixels."""
    if region.type not in INSET or bpy.context.preferences.themes[0].regions.sidebars.back[3] > 0.05:
        return 0
    return round(INSET[region.type] * bpy.context.preferences.system.ui_scale)


def _clear(area, blank=False):
    """The part of a viewport's canvas that no panel hides, as (left, bottom, right, top) in pixels
    from the middle of the canvas, which is where both halves show the same point. `blank` is for the reference
    half, whose bars draw nothing: where they float over the canvas, it shows through them.

    Read from the layout alone, whatever the colour theme: Blender floats a bar over the canvas only while its colour
    is see-through, and otherwise lays the canvas out beside it, which the position test below finds."""
    canvas = common.window_region(area)
    left, bottom, right, top = 0, 0, canvas.width, canvas.height
    for region in area.regions:
        if region.type == 'WINDOW' or region.width <= 1 or region.height <= 1:  # hidden ones are a pixel wide
            continue
        if region.type in SEE_THROUGH or (blank and region.type in {'HEADER', 'TOOL_HEADER'}):
            continue  # beside the canvas it is not over it; over it, the canvas shows through
        if (region.x >= canvas.x + canvas.width or region.x + region.width <= canvas.x
                or region.y >= canvas.y + canvas.height or region.y + region.height <= canvas.y):
            continue  # beside the canvas, not over it
        if region.alignment == 'LEFT':
            left = max(left, region.x + region.width - _inset(region) - canvas.x)
        elif region.alignment == 'RIGHT':
            right = min(right, region.x + _inset(region) - canvas.x)
        elif region.alignment == 'BOTTOM':
            bottom = max(bottom, region.y + region.height - canvas.y)
        elif region.alignment == 'TOP':
            top = min(top, region.y - canvas.y)
    x, y = canvas.width / 2, canvas.height / 2
    return left - x, bottom - y, right - x, top - y


def visible_part(area):
    """The part of a viewport's canvas that no panel hides, as (left, bottom, right, top) in pixels from its middle:
    what the eye sees of it, between the toolbar, sidebar, bars and shelf. Frame Sculpt (zbrush_keys) fits to it."""
    return _clear(area)


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
    x0 = _pixel(x + frame[0]) if frame[0] - own[0] > SLIVER else 0
    x1 = _pixel(x + frame[2]) if own[2] - frame[2] > SLIVER else width
    strips = []
    if x0 > 0:
        strips.append((0, 0, x0, height))
    if x1 < width:
        strips.append((x1, 0, width, height))
    if frame[1] - own[1] > SLIVER:
        strips.append((x0, 0, x1, _pixel(y + frame[1])))
    if own[3] - frame[3] > SLIVER:
        strips.append((x0, _pixel(y + frame[3]), x1, height))
    return strips


def _pixel(position):
    """A position measured from a canvas's middle, back on the pixel grid: the halves' canvases can differ in
    width, so the middle can fall between two pixels."""
    return math.floor(position + 0.5)


def panes(screen, area):
    """With Panes Under Panels, the strips of the sculpt half's canvas beyond the frame both halves share, down its
    right side and along its bottom, as (x0, y0, x1, y1) in canvas pixels: under its sidebar and brush shelf, which
    float over the canvas in pieces and leave its edges broken. Shaded as the reference half's are, the edges then
    run the full length in both halves."""
    halves = _halves(screen) if screen.get(MARK) else None
    if (halves is None or area != halves[1] or not bpy.context.scene.sculpt_kit_reference_panes
            or area.spaces.active.region_3d.view_perspective == 'CAMERA'):
        return []
    frame = shared_frame(screen)
    canvas = common.window_region(area)
    width, height = canvas.width, canvas.height
    right, bottom = _pixel(width / 2 + frame[2]), _pixel(height / 2 + frame[1])
    strips = []
    if width - right > SLIVER:
        strips.append((right, 0, width, height))
    if bottom > SLIVER:
        strips.append((0, 0, min(right, width), bottom))
    return strips


def _shelf_tabs_pane():
    """The sculpt half's brush shelf tabs as one pane, as the ZBrush look's colour theme draws them, where it does
    not: Blender draws a backdrop behind the tabs alone, which leaves the shelf no straight top edge to match."""
    from .zbrush_look import theme
    if theme._pane_handle is None:
        theme._draw_pane()


def _draw_frame():
    """Runs over each 3D viewport once it is drawn: dims what this half shows and the other does not, and with
    Panes Under Panels the sculpt half beyond the frame they share."""
    context = bpy.context
    screen, area = context.screen, context.area
    if screen is None or area is None or not screen.get(MARK) or not context.scene.sculpt_kit_reference_frame:
        return
    halves = _halves(screen)
    if halves is not None and area == halves[1]:
        _shelf_tabs_pane()
    strips = bands(screen, area)
    strips += [strip for strip in panes(screen, area) if strip not in strips]
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
    for x0, y0, x1, y1 in strips:  # on the strip's own outermost pixels along the side that faces the middle
        if y0 == 0 and y1 == canvas.height:
            ends += [(x1 - 0.5, y0), (x1 - 0.5, y1)] if x0 == 0 else [(x0 + 0.5, y0), (x0 + 0.5, y1)]
        else:
            ends += [(x0, y1 - 0.5), (x1, y1 - 0.5)] if y0 == 0 else [(x0, y0 + 0.5), (x1, y0 + 0.5)]
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
    # Closing hid it by its eye in the Outliner; the reference half shows it again
    next(layer for i, layer, _parent in numbered if i == index).hide_viewport = False

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
    # It was split off the sculpt's viewport and has its Color: Attribute or Texture would show a scan's own colour
    # layers, which glow purple and yellow on Cast Hall's casts, and its blank bars leave no way to change it
    if reference_space.shading.color_type in {'VERTEX', 'TEXTURE'}:
        reference_space.shading.color_type = 'MATERIAL'
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
    _shadings[screen.as_pointer()] = _shading(sculpt_area)
    return {'FINISHED'}


def _hide_reference(scene, view_layer):
    """Once the halves are one ordinary viewport again, hide the reference by its eye in the Outliner, where
    one click brings it back: the viewport goes on showing what the sculpt half showed. Shown, a reference
    that stands where the sculpt does pokes through it, in its own colours when the viewport is in Attribute."""
    reference = scene.sculpt_kit_reference
    layer = next((layer for _i, layer, _parent in _layer_collections(view_layer) if layer.collection == reference), None)
    if reference is not None and layer is not None:
        layer.hide_viewport = True


def _tidy_broken():
    """Put back the half left of a reference view whose other half was closed with Blender's own Close Area rather
    than the button: an ordinary viewport again, with the reference hidden by its eye (the reference half also gets
    its overlays, gizmos and toolbar back), and the screen no longer marked as having the view open."""
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        if not screen.get(MARK) or _halves(screen) is not None:
            continue
        known = _halves_of.get(screen.as_pointer())
        areas = _view3d_areas(screen)
        if known:
            left = [a for a in areas if a.as_pointer() in known]
        else:  # after a file was loaded: the one viewport with collections of its own, if only one has them
            left = [a for a in areas if a.spaces.active.use_local_collections]
            left = left if len(left) == 1 else []
        for area in left:
            space = area.spaces.active
            space.use_local_collections = False
            if known and area.as_pointer() == known[0]:
                space.overlay.show_overlays = True
                space.show_gizmo = True
                space.show_region_toolbar = True
            area.tag_redraw()
        if left:
            _hide_reference(window.scene, window.view_layer)
        _forget(screen)
    return None


def _close(context):
    screen = context.screen
    reference_area, sculpt_area = _halves(screen)
    with context.temp_override(area=reference_area, region=common.window_region(reference_area)):
        bpy.ops.screen.area_close()
    sculpt_area.spaces.active.use_local_collections = False
    _hide_reference(context.scene, context.view_layer)
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
        if is_open(context.screen):
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
        sub = col.column()
        sub.active = context.scene.sculpt_kit_reference_frame
        sub.prop(context.scene, "sculpt_kit_reference_panes")


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
    bpy.types.Scene.sculpt_kit_reference_panes = BoolProperty(
        name="Panes Under Panels", default=True, update=lambda _self, _context: common.redraw_all(),
        description="With Matching Edges, shade the sculpt half too beyond the frame both halves share, down its "
                    "right side and along its bottom, under the sidebar and brush shelf: the edges then run the full "
                    "length in both halves")
    for cls in _classes:
        bpy.utils.register_class(cls)
    _blank_bars()
    _handle = bpy.types.SpaceView3D.draw_handler_add(_keep_in_step, (), 'WINDOW', 'PRE_VIEW')
    _frame_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_frame, (), 'WINDOW', 'POST_PIXEL')


def disable():
    global _handle, _frame_handle
    if bpy.app.timers.is_registered(_tidy_broken):
        bpy.app.timers.unregister(_tidy_broken)
    for handle in (_handle, _frame_handle):
        if handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handle = _frame_handle = None
    _unblank_bars()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.sculpt_kit_reference_panes
    del bpy.types.Scene.sculpt_kit_reference_frame
    del bpy.types.Scene.sculpt_kit_reference
