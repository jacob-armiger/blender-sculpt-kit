"""Material: the matcap new sculpts and the ZBrush layout start in.

There are three to pick from: red wax, grey and brown clay. Each style of the
look remembers its own pick, so ZBrush can stay on grey while KlaySculpt keeps
its clay. Picking one, or changing style, also moves the viewports that are
showing one of the three over to it; switching the part off returns them to
Blender's plain matcap.

Red wax and brown clay ship with Blender. The grey is the kit's own, lit like
ZBrush's MatCap Gray (tools/build_matcap.py makes it), and is loaded into
Blender's list of matcaps while the part is on.
"""
import os

import bpy
from bpy.props import EnumProperty

from ... import common
from .. import brush_library

MATERIALS = (
    ('RED_WAX', "Red wax", "ZBrush's starting material"),
    ('GREY', "Grey", "A neutral grey, lit like ZBrush's MatCap Gray"),
    ('BROWN_CLAY', "Brown clay", "KlaySculpt's clay"),
)
MATCAPS = {'RED_WAX': "red_wax.exr", 'GREY': "sculptkit_grey.exr", 'BROWN_CLAY': "clay_brown.exr"}
PLAIN = "basic_bright.exr"  # what Blender's own Sculpting workspace uses
OWN = os.path.join(brush_library.ASSETS, "matcaps")  # matcaps the kit brings with it

_on = False


def choice_property(style):
    """The preference that holds a style's material."""
    return "look_material_" + style.lower()


def _lights():
    return bpy.context.preferences.studio_lights


def _has(name):
    return any(light.type == 'MATCAP' and light.name == name for light in _lights())


def _own_files():
    return sorted(name for name in os.listdir(OWN) if name.endswith(".exr")) if os.path.isdir(OWN) else []


def _load_own():
    for name in _own_files():
        if not _has(name):
            _lights().load(os.path.join(OWN, name), 'MATCAP')


def _unload_own():
    names = set(_own_files())
    for light in [light for light in _lights() if light.type == 'MATCAP' and light.name in names]:
        _lights().remove(light)


def matcap():
    """The matcap picked for the current style, or None while the part is off or Blender lacks it."""
    prefs = common.prefs()
    if not _on or prefs is None:
        return None
    name = MATCAPS[getattr(prefs, choice_property(prefs.look_style))]
    return name if _has(name) else None


def apply(shading):
    """Give a viewport the picked material. It has to be on matcap lighting already."""
    name = matcap()
    if name is not None and shading.light == 'MATCAP':
        shading.studio_light = name


def _matcap_viewports():
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == 'VIEW_3D' and space.shading.light == 'MATCAP':
                    yield space.shading


def restyle():
    """The pick or the style changed: move the viewports showing one of the kit's materials over."""
    for shading in _matcap_viewports():
        if shading.studio_light in MATCAPS.values():
            apply(shading)


class SCULPTKIT_OT_material(bpy.types.Operator):
    """Sculpt in this material, and start new sculpts in it"""
    bl_idname = "sculptkit.material"
    bl_label = "Sculpt Kit Material"
    bl_options = {'INTERNAL'}

    material: EnumProperty(items=MATERIALS)

    @classmethod
    def poll(cls, context):
        return context.space_data is not None and context.space_data.type == 'VIEW_3D'

    def execute(self, context):
        prefs = common.prefs()
        setattr(prefs, choice_property(prefs.look_style), self.material)  # moves the other viewports as well
        shading = context.space_data.shading
        shading.light = 'MATCAP'
        apply(shading)
        return {'FINISHED'}


def enable(by_user=False):
    global _on
    _on = True
    _load_own()
    bpy.utils.register_class(SCULPTKIT_OT_material)
    if by_user:
        for shading in _matcap_viewports():
            apply(shading)


def disable(restore=True):
    global _on
    _on = False
    bpy.utils.unregister_class(SCULPTKIT_OT_material)
    if not restore:
        return
    if _has(PLAIN):
        for shading in _matcap_viewports():
            if shading.studio_light in MATCAPS.values():
                shading.studio_light = PLAIN
    _unload_own()
