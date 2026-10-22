"""The bundled brush library.

A folder under assets/ that is listed in Blender's asset libraries while the
feature is on, which is what puts the Sculpt Kit tab in the Sculpt Mode brush
shelf.
"""
import os

import bpy

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

KIT = ("Sculpt Kit", os.path.join(ASSETS, "kit"))
# Versions before 0.5 kept the ported brushes in a library of their own. Its entry is taken away when found.
RETIRED = ("Sculpt Kit Kingslien",)


def _libraries():
    return bpy.context.preferences.filepaths.asset_libraries


def _find(path):
    path = os.path.normpath(path)
    return next((lib for lib in _libraries() if os.path.normpath(bpy.path.abspath(lib.path)) == path), None)


def _has_brushes(path):
    return os.path.isdir(path) and any(name.endswith(".blend") for name in os.listdir(path))


def _remove_retired():
    for library in [lib for lib in _libraries() if lib.name in RETIRED]:
        _libraries().remove(library)


def enable():
    _remove_retired()
    name, path = KIT
    if _has_brushes(path) and _find(path) is None:
        _libraries().new(name=name, directory=path)


def disable():
    _remove_retired()
    found = _find(KIT[1])
    if found is not None:
        _libraries().remove(found)
