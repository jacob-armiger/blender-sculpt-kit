"""Sculpt Kit: a ZBrush-style sculpting setup for Blender.

Each feature lives in its own module under features/ and has its own switch
in the add-on preferences, so any of them can be turned off on its own.
"""
import bpy

from . import features, preferences


def _sync_later():
    features.sync_all()
    return None


def register():
    preferences.register()
    if features.sync_all() is False:
        # Preferences are not reachable yet while the add-on is first being
        # enabled; apply the switches once Blender has finished enabling it.
        bpy.app.timers.register(_sync_later, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(_sync_later):
        bpy.app.timers.unregister(_sync_later)
    features.disable_all()
    preferences.unregister()
