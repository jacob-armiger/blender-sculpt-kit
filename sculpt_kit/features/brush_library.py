"""The bundled brush libraries.

Each library is a folder under assets/ that is listed in Blender's asset
libraries while its feature is on, which is what makes its brushes show up in
the Sculpt Mode brush shelf.
"""
import os

import bpy

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

KIT = ("Sculpt Kit", os.path.join(ASSETS, "kit"))
KINGSLIEN = ("Sculpt Kit Kingslien", os.path.join(ASSETS, "kingslien"))


def _libraries():
    return bpy.context.preferences.filepaths.asset_libraries


def _find(path):
    path = os.path.normpath(path)
    return next((lib for lib in _libraries() if os.path.normpath(bpy.path.abspath(lib.path)) == path), None)


def _has_brushes(path):
    return os.path.isdir(path) and any(name.endswith(".blend") for name in os.listdir(path))


def _add(library):
    name, path = library
    if _has_brushes(path) and _find(path) is None:
        _libraries().new(name=name, directory=path)


def _remove(library):
    found = _find(library[1])
    if found is not None:
        _libraries().remove(found)


def enable_kit():
    _add(KIT)


def disable_kit():
    _remove(KIT)


def enable_kingslien():
    _add(KINGSLIEN)


def disable_kingslien():
    _remove(KINGSLIEN)
