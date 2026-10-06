"""Shared helpers for the interactive tests (tests/sim_*.py).

A test is a generator. It simulates a few mouse or key events, yields the
number of seconds Blender should keep running before the next step, and
records checks. Results go to $SCULPT_KIT_TEST_OUT/<test name>.txt.

Blender must be started with --enable-event-simulate.
"""
import os
import sys
import traceback

import bpy
import numpy as np
from bpy_extras import view3d_utils
from mathutils import Vector

OUT = os.environ.get("SCULPT_KIT_TEST_OUT", "/tmp")
MODULE = "bl_ext.user_default.sculpt_kit"


class Sim:
    def __init__(self, name):
        self.name = name
        self.lines = []

    # ---------------------------------------------------------------- results
    def check(self, label, ok, detail=""):
        line = ("PASS  " if ok else "FAIL  ") + label + ("  [%s]" % (detail,) if detail != "" else "")
        self.lines.append(line)
        print(line)
        sys.stdout.flush()

    def note(self, text):
        self.lines.append("      " + text)
        print(text)

    def save(self):
        with open(os.path.join(OUT, self.name + ".txt"), "w") as f:
            f.write("\n".join(self.lines) + "\n")

    # ---------------------------------------------------------------- context
    @property
    def window(self):
        return bpy.context.window_manager.windows[0]

    def view3d_areas(self):
        return sorted((a for a in self.window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.x)

    @property
    def area(self):
        return max(self.view3d_areas(), key=lambda a: a.width * a.height)

    def region(self, area=None):
        return next(r for r in (area or self.area).regions if r.type == 'WINDOW')

    def space(self, area=None):
        return (area or self.area).spaces.active

    def rv3d(self, area=None):
        return self.space(area).region_3d

    def override(self, area=None):
        area = area or self.area
        return bpy.context.temp_override(window=self.window, area=area, region=self.region(area))

    @property
    def prefs(self):
        return bpy.context.preferences.addons[MODULE].preferences

    # ------------------------------------------------------------------ state
    def positions(self, ob=None):
        mesh = (ob or bpy.context.active_object).data
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        return co.reshape(-1, 3)

    def mask(self, ob=None):
        mesh = (ob or bpy.context.active_object).data
        attribute = mesh.attributes.get(".sculpt_mask")
        values = np.zeros(len(mesh.vertices))
        if attribute is not None:
            attribute.data.foreach_get("value", values)
        return values

    def view(self, area=None):
        rv3d = self.rv3d(area)
        return rv3d.view_rotation.copy(), rv3d.view_location.copy(), rv3d.view_distance

    def snapshot(self):
        rotation, location, distance = self.view()
        return {"co": self.positions(), "mask": self.mask(), "rotation": rotation, "location": location,
                "distance": distance}

    def changes(self, before):
        """Which parts of the state differ from an earlier snapshot."""
        now = self.snapshot()
        return {
            "mesh": bool(np.abs(now["co"] - before["co"]).max() > 1e-6),
            "mask": bool(np.abs(now["mask"] - before["mask"]).max() > 1e-4),
            "rotated": now["rotation"].rotation_difference(before["rotation"]).angle > 0.01,
            "moved": (now["location"] - before["location"]).length > 1e-4,
            "zoomed": abs(now["distance"] - before["distance"]) > 1e-4,
        }

    # ----------------------------------------------------------------- points
    def at(self, fx, fy, area=None):
        """Window coordinates of a point given as fractions of the viewport (0, 0 = bottom left)."""
        region = self.region(area)
        return int(region.x + fx * region.width), int(region.y + fy * region.height)

    def project(self, location, area=None):
        """Window coordinates of a 3D point."""
        region = self.region(area)
        point = view3d_utils.location_3d_to_region_2d(region, self.rv3d(area), Vector(location))
        return int(region.x + point.x), int(region.y + point.y)

    # ----------------------------------------------------------------- events
    def _send(self, kind, value, xy, mods):
        self.window.event_simulate(type=kind, value=value, x=xy[0], y=xy[1],
                                   shift=mods.get("shift", False), ctrl=mods.get("ctrl", False),
                                   alt=mods.get("alt", False), oskey=mods.get("oskey", False))

    def drag(self, start, end, button='LEFTMOUSE', steps=20, **mods):
        """Press at start, move to end, release. Use with `yield from`."""
        self._send('MOUSEMOVE', 'NOTHING', start, mods)
        yield 0.05
        self._send(button, 'PRESS', start, mods)
        yield 0.05
        for i in range(1, steps + 1):
            t = i / steps
            point = (int(start[0] + (end[0] - start[0]) * t), int(start[1] + (end[1] - start[1]) * t))
            self._send('MOUSEMOVE', 'NOTHING', point, mods)
            yield 0.012
        self._send(button, 'RELEASE', end, mods)
        yield 0.35

    def click(self, point, button='LEFTMOUSE', **mods):
        yield from self.drag(point, point, button, steps=1, **mods)

    def key(self, key, at=None, **mods):
        point = at or self.at(0.5, 0.5)
        self._send('MOUSEMOVE', 'NOTHING', point, {})
        yield 0.05
        self._send(key, 'PRESS', point, mods)
        yield 0.05
        self._send(key, 'RELEASE', point, mods)
        yield 0.3

    def call(self, operator, area=None, **properties):
        """Run an operator the way a click on its button does, recording an undo step."""
        with self.override(area):
            return operator('EXEC_DEFAULT', True, **properties)

    def undo(self):
        """The keyboard's undo. Use with `yield from`."""
        yield from self.key('Z', oskey=sys.platform == "darwin", ctrl=sys.platform != "darwin")
        yield 0.5

    def screenshot(self, filename):
        path = os.path.join(OUT, filename)
        with bpy.context.temp_override(window=self.window):
            bpy.ops.screen.screenshot(filepath=path)
        return path


def run(name, test):
    """Run test(sim) as a generator driven by a timer, then save the results and quit Blender."""
    sim = Sim(name)
    steps = test(sim)

    def tick():
        try:
            return next(steps)
        except StopIteration:
            pass
        except Exception:
            sim.lines.append("ERROR  " + traceback.format_exc())
            print(traceback.format_exc())
        sim.save()
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(tick, first_interval=1.5)
