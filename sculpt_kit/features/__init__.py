"""Feature registry.

A feature is a module with enable() and disable(). Its switch in the add-on
preferences is the BoolProperty `use_<id>` (see preferences.py).
"""
from collections import namedtuple

from .. import common
from . import brush_library, brush_tuning, reference_view, remesh, sculpt_session, subdivide, zbrush_keys, zbrush_look

Feature = namedtuple("Feature", "id label description enable disable")

FEATURES = (
    Feature("zbrush_keys", "ZBrush navigation and keys",
            "Sculpt Mode only: right-click and empty-canvas navigation, Alt inverts, Ctrl masks, ZBrush hotkeys",
            zbrush_keys.enable, zbrush_keys.disable),
    Feature("zbrush_look", "ZBrush look",
            "Colour theme, layout, shelf, subtool list and material that make Blender read like ZBrush or KlaySculpt",
            zbrush_look.enable, zbrush_look.disable),
    Feature("kit_brushes", "Sculpt Kit brushes",
            "A Sculpt Kit tab in the brush shelf: clay, rakes, knives, hair texture and a Move that grabs through the model",
            brush_library.enable, brush_library.disable),
    Feature("reference_view", "Synced reference view",
            "Sidebar button that splits the viewport: reference on the left, sculpt on the right, same view, same frame",
            reference_view.enable, reference_view.disable),
    Feature("sculpt_session", "New sculpt sphere, cube or cylinder",
            "Sidebar buttons that add a sphere, rounded cube or thin armature cylinder of about 25 thousand quads or triangles",
            sculpt_session.enable, sculpt_session.disable),
    Feature("remesh", "Remesh tools",
            "Voxel Remesh by resolution on Ctrl + R, like DynaMesh, and a Quad Remesh that repairs the mesh first",
            remesh.enable, remesh.disable),
    Feature("subdivide", "Subdivide",
            "Sidebar panel for subdivision levels like ZBrush's: Divide, Lower / Higher, SDiv, and a plain Subdivide Mesh",
            subdivide.enable, subdivide.disable),
    Feature("brush_tuning", "Brush tuning (for development)",
            "Sidebar buttons that copy the active brush's settings or return it to its saved ones",
            brush_tuning.enable, brush_tuning.disable),
)
BY_ID = {f.id: f for f in FEATURES}

_enabled = set()


def is_enabled(feature_id):
    return feature_id in _enabled


def set_enabled(feature_id, state):
    feature = BY_ID[feature_id]
    if state and feature_id not in _enabled:
        feature.enable()
        _enabled.add(feature_id)
    elif not state and feature_id in _enabled:
        feature.disable()
        _enabled.discard(feature_id)


def sync_all():
    """Bring every feature in line with its preference switch. False if preferences are not available yet."""
    prefs = common.prefs()
    if prefs is None:
        return False
    for feature in FEATURES:
        set_enabled(feature.id, getattr(prefs, "use_" + feature.id))
    return True


def disable_all():
    for feature_id in list(_enabled):
        set_enabled(feature_id, False)
