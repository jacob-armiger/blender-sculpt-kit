"""Colour theme: dresses Blender's theme as ZBrush or KlaySculpt, and puts it back.

The values come from themes.json, which tools/build_theme.py generates. Only
the preferences a style changes are touched. What they were before is kept in
the add-on's own preferences, so the theme and the way back from it are always
saved to disk together.
"""
import json
import os

import bpy

from ... import common

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "themes.json")
TOLERANCE = 0.006  # theme colours are stored in 8 bits

_data = None


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


def enable(by_user=False):
    prefs = common.prefs()
    style = prefs.look_style
    worn = wearing()
    if worn == style:
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
    _write({path: value for path, value in data()["styles"][style].items() if path in before})


def disable(restore=True):
    if not restore:
        return
    prefs = common.prefs()
    worn = wearing()
    if worn is not None:
        _take_off(worn, prefs)
    elif prefs is not None:
        prefs.look_theme_backup = ""  # the theme has been changed by hand since: nothing of ours is left to undo
