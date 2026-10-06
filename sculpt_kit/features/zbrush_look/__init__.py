"""ZBrush look: the colours, layout, shelf, subtool list and material that make Blender read like ZBrush.

The look comes in two styles, ZBrush and KlaySculpt, which set the colour theme
and which material is in use. Each part is a module with enable() and disable()
and has its own switch in the add-on preferences, under the feature's main
switch.

Parts that change Blender's own settings (the theme, Region Overlap, a
viewport's material, the workspaces in a file) put them back when switched off.
They leave them alone while Blender is only shutting the add-on down on its way
out, so that what was saved to disk stays in step with the switches.
"""
from collections import namedtuple
from contextlib import contextmanager

from ... import common
from . import layout, material, shelf, subtools, theme

STYLES = (
    ('ZBRUSH', "ZBrush", "Dark grey shelves beside the canvas, orange highlights, a black-to-grey canvas"),
    ('KLAY', "KlaySculpt", "Slate panels floating over a night-blue canvas, coral highlights"),
)

Part = namedtuple("Part", "id switch module")
PARTS = (
    Part("theme", "look_theme", theme),
    Part("layout", "look_layout", layout),
    Part("shelf", "look_shelf", shelf),
    Part("subtools", "look_subtools", subtools),
    Part("material", "look_material", material),
)

# Preference switch -> the lines shown under it in the add-on preferences.
OPTIONS = (
    ("look_theme", (
        "Dresses Blender's theme in the style above. ZBrush also turns Region Overlap off",
        "Switching this off puts your own theme and setting back",
    )),
    ("look_layout", (
        "ZBrush Layout button in the top bar: one big canvas with the Sculpt Kit tray beside it",
        "Press it again to go back. Each file gets the workspace the first time you press it there",
    )),
    ("look_shelf", (
        "Sculpt Mode brush bar: Brush, Stroke, Alpha, Zadd / Zsub, Z Intensity, Draw Size",
        "Viewport header: Persp, Floor, PolyF, Transp, Solo and a material picker",
    )),
    ("look_subtools", (
        "Subtools panel in the Sculpt Kit tab: click a mesh to work on it, eye to hide it",
        "Duplicate, Merge Down and Delete for the active one",
    )),
    ("look_material", (
        "New sculpts and the ZBrush layout start in the material picked here. Each style remembers its own",
        "The grey is lit like ZBrush's MatCap Gray. The material picker in the viewport header changes the pick too",
    )),
)

_on = False       # the feature's main switch
_active = set()   # ids of the parts that are enabled
_by_user = False


@contextmanager
def user_action():
    """What happens inside was asked for with a switch, as opposed to Blender starting up.

    Parts use it to tell whether to act on what is already on screen."""
    global _by_user
    _by_user = True
    try:
        yield
    finally:
        _by_user = False


def refresh():
    """Bring every part in line with the main switch and its own."""
    prefs = common.prefs()
    restore = not common.switching_all_addons_off()
    for part in PARTS:
        wanted = _on and prefs is not None and getattr(prefs, part.switch)
        if wanted and part.id not in _active:
            part.module.enable(by_user=_by_user)
            _active.add(part.id)
        elif not wanted and part.id in _active:
            part.module.disable(restore=restore)
            _active.discard(part.id)


def restyle():
    """The style was changed: move the theme and the material over to it."""
    if "theme" in _active:
        theme.enable()
    if "material" in _active:
        material.restyle()


def enable():
    global _on
    _on = True
    refresh()


def disable():
    global _on
    _on = False
    refresh()
