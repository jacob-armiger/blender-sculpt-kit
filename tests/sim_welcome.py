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
    sim.prefs.use_sculpt_session = False  # the dialog's defaults turn it on: the sign that Done was pressed
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
    sim.check("the dialog was open, and Done applied its choice: every tool on, the colour theme left off",
              sim.prefs.use_sculpt_session and sim.prefs.welcome_done and not sim.prefs.look_theme,
              (sim.prefs.use_sculpt_session, sim.prefs.welcome_done, sim.prefs.look_theme))
    sim.prefs.look_theme = True  # as the other tests expect to find it
    sim.prefs.look_style = 'ZBRUSH'


simlib.run("sim_welcome", test)
