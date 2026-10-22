"""Interactive test: the welcome dialog opens over the window, and Done applies what was picked."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import simlib  # noqa: E402


def test(sim):
    welcome = importlib.import_module(simlib.MODULE + ".welcome")
    sim.prefs.welcome_done = False
    sim.prefs.look_theme = False
    point = sim.at(0.5, 0.5)
    sim._send('MOUSEMOVE', 'NOTHING', point, {})
    yield 0.3
    bpy.app.timers.register(welcome._welcome, first_interval=0.1)
    yield 1.5
    sim.screenshot("welcome.png")
    sim._send('RET', 'PRESS', point, {})
    yield 0.1
    sim._send('RET', 'RELEASE', point, {})
    yield 1.0
    sim.check("the dialog was open, and Done applied its choice", sim.prefs.look_theme and sim.prefs.look_style == 'KLAY',
              (sim.prefs.look_theme, sim.prefs.look_style))
    sim.prefs.look_style = 'ZBRUSH'  # as the other tests expect to find it


simlib.run("sim_welcome", test)
