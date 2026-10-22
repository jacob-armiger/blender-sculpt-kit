"""Colour theme: dresses Blender's theme as ZBrush or KlaySculpt, and puts it back.

The values come from themes.json, which tools/build_theme.py generates. Only
the preferences a style changes are touched. What they were before is kept in
the add-on's own preferences, so the theme and the way back from it are always
saved to disk together.

Where the brush shelf floats over the canvas, the strip of tabs above it gets a
pane the width of the shelf, with a border along the top of the strip, so that
the shelf reads as one pane. Blender draws a backdrop behind the tabs alone, and
an add-on cannot widen it; the pane is drawn on the canvas, under the tabs.
"""
import json
import os

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ... import common

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "themes.json")
TOLERANCE = 0.006  # theme colours are stored in 8 bits

_data = None
_pane_handle = None
BORDER = (1.0, 1.0, 1.0, 0.12)


def data():
    global _data
    if _data is None:
        with open(FILE) as f:
            _data = json.load(f)
    return _data


def _read(path):
    value = bpy.context.preferences.path_resolve(path)
    return value if isinstance(value, (bool, int, float, str)) else list(value)


def _write(values):
    preferences = bpy.context.preferences
    for path, value in values.items():
        owner, _, name = path.rpartition(".")
        try:
            setattr(preferences.path_resolve(owner), name, value)
        except (ValueError, TypeError, AttributeError):
            pass  # a preference this version of Blender does not have


def _in_place(path, value):
    try:
        current = _read(path)
    except ValueError:
        return False
    if isinstance(value, list):
        return len(current) == len(value) and all(abs(a - b) < TOLERANCE for a, b in zip(current, value))
    if isinstance(value, float):
        return abs(current - value) < TOLERANCE
    return current == value


def _version(style):
    """A short fingerprint of the style's values, to tell when a newer kit has changed them."""
    import hashlib
    return hashlib.sha1(json.dumps(data()["styles"][style], sort_keys=True).encode()).hexdigest()[:12]


def wearing():
    """The style Blender's theme is dressed in, or None.

    More than half of a style's values count as wearing it, so that a few
    colours adjusted by hand afterwards do not hide it."""
    for style, values in data()["styles"].items():
        if sum(_in_place(path, value) for path, value in values.items()) > len(values) / 2:
            return style
    return None


def _take_off(style, prefs):
    """Put back what the style changed: the saved values, or Blender's defaults where none were saved."""
    saved = json.loads(prefs.look_theme_backup) if prefs is not None and prefs.look_theme_backup else {}
    blender = data()["blender"]
    _write({path: saved.get(path, blender[path]) for path in data()["styles"][style]})
    if prefs is not None:
        prefs.look_theme_backup = ""


def _draw_pane():
    """Runs over each 3D viewport's canvas, before the shelf floating over it is drawn."""
    context = bpy.context
    area, canvas = context.area, context.region
    if area is None or canvas is None or not context.preferences.system.use_region_overlap:
        return
    tabs = next((r for r in area.regions if r.type == 'ASSET_SHELF_HEADER' and r.height > 1 and r.width > 1), None)
    if tabs is None:
        return
    x0, x1 = tabs.x - canvas.x, tabs.x - canvas.x + tabs.width
    y0, y1 = tabs.y - canvas.y, tabs.y - canvas.y + tabs.height  # the border runs along the top of the strip
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    shader.uniform_float("color", tuple(context.preferences.themes[0].regions.asset_shelf.back))
    batch_for_shader(shader, 'TRIS', {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]},
                     indices=[(0, 1, 2), (0, 2, 3)]).draw(shader)
    shader.uniform_float("color", BORDER)
    batch_for_shader(shader, 'LINES', {"pos": [(x0, y1 - 0.5), (x1, y1 - 0.5)]}).draw(shader)
    gpu.state.blend_set('NONE')


def _show_pane(on):
    global _pane_handle
    if on and _pane_handle is None:
        _pane_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_pane, (), 'WINDOW', 'POST_PIXEL')
    elif not on and _pane_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_pane_handle, 'WINDOW')
        _pane_handle = None
    common.redraw_all()


def enable(by_user=False):
    _show_pane(True)
    prefs = common.prefs()
    style = prefs.look_style
    worn = wearing()
    values = data()["styles"][style]
    version = _version(style)
    if worn == style:
        # Already on. If this version of the kit changed the style, its values go on again; otherwise colours
        # adjusted by hand since are left as they are.
        if prefs.look_theme_applied != version:
            _write({path: value for path, value in values.items() if not _in_place(path, value)})
            prefs.look_theme_applied = version
        return
    if worn is not None:
        _take_off(worn, prefs)  # changing style: back to the user's own theme first, so that is what gets saved
    before = {}
    for path in data()["styles"][style]:
        try:
            before[path] = _read(path)
        except ValueError:
            pass
    prefs.look_theme_backup = json.dumps(before)
    _write({path: value for path, value in values.items() if path in before})
    prefs.look_theme_applied = version


def disable(restore=True):
    _show_pane(False)
    if not restore:
        return
    prefs = common.prefs()
    worn = wearing()
    if worn is not None:
        _take_off(worn, prefs)
    elif prefs is not None:
        prefs.look_theme_backup = ""  # the theme has been changed by hand since: nothing of ours is left to undo
