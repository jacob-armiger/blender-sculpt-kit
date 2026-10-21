import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import common
from .features.zbrush_look import STYLES as LOOK_STYLES
from .features.zbrush_look.material import MATERIALS as LOOK_MATERIALS


def _feature_switch(feature_id):
    def update(self, _context):
        from . import features
        features.set_enabled(feature_id, getattr(self, "use_" + feature_id))
    return update


def _rebuild_keys(_self, _context):
    from . import features
    from .features import zbrush_keys
    if features.is_enabled("zbrush_keys"):
        zbrush_keys.rebuild()


def _look_switch(self, _context):
    from . import features
    from .features import zbrush_look
    with zbrush_look.user_action():
        features.set_enabled("zbrush_look", self.use_zbrush_look)


def _look_part(_self, _context):
    from . import features
    from .features import zbrush_look
    if features.is_enabled("zbrush_look"):
        with zbrush_look.user_action():
            zbrush_look.refresh()


def _look_style(_self, _context):
    from . import features
    from .features import zbrush_look
    if features.is_enabled("zbrush_look"):
        with zbrush_look.user_action():
            zbrush_look.restyle()


class SculptKitPreferences(bpy.types.AddonPreferences):
    bl_idname = common.PACKAGE

    use_zbrush_keys: BoolProperty(name="ZBrush navigation and keys", default=True,
                                  update=_feature_switch("zbrush_keys"))
    use_zbrush_look: BoolProperty(name="ZBrush look", default=True, update=_look_switch)
    use_kit_brushes: BoolProperty(name="Move and rake brushes", default=True,
                                  update=_feature_switch("kit_brushes"))
    use_kingslien_brushes: BoolProperty(name="Kingslien brushes", default=True,
                                        update=_feature_switch("kingslien_brushes"))
    use_reference_view: BoolProperty(name="Synced reference view", default=True,
                                     update=_feature_switch("reference_view"))
    use_sculpt_session: BoolProperty(name="New sculpt sphere, cube or cylinder", default=True,
                                     update=_feature_switch("sculpt_session"))
    use_remesh: BoolProperty(name="Remesh tools", default=True,
                             update=_feature_switch("remesh"))
    use_subdivide: BoolProperty(name="Subdivide", default=True,
                                update=_feature_switch("subdivide"))
    use_brush_tuning: BoolProperty(name="Brush tuning helper", default=True,
                                   update=_feature_switch("brush_tuning"))

    remesh_canvas_gesture: BoolProperty(
        name="Ctrl + drag a box on empty canvas remeshes", default=False,
        description="ZBrush's DynaMesh gesture: with nothing masked, a Ctrl + drag box that misses the model runs "
                    "Voxel Remesh instead of clearing the mask. Needs the ZBrush modifier keys")

    # Parts of the ZBrush keys that can be switched separately.
    keys_right_click: BoolProperty(
        name="Right-click navigation", default=True, update=_rebuild_keys,
        description="Right-drag rotates, Alt+right-drag moves, Ctrl+right-drag zooms. "
                    "Blender's right-click menu moves to Space")
    keys_canvas: BoolProperty(
        name="Empty-canvas navigation", default=True, update=_rebuild_keys,
        description="Dragging on the background rotates the view instead of waiting to reach the model")
    keys_modifiers: BoolProperty(
        name="Alt inverts, Ctrl masks", default=True, update=_rebuild_keys,
        description="ZBrush modifier keys in place of Blender's Ctrl to invert")
    keys_hotkeys: BoolProperty(
        name="ZBrush hotkeys", default=True, update=_rebuild_keys,
        description="S size, U strength, F frame, X symmetry and the other single-key shortcuts")

    # The ZBrush look: its style, and the parts that can be switched separately.
    look_style: EnumProperty(
        name="Style", items=LOOK_STYLES, default='ZBRUSH', update=_look_style,
        description="Which program the colour theme and the material take after")
    look_theme: BoolProperty(
        name="Colour theme", default=True, update=_look_part,
        description="Dress Blender's theme in the chosen style. Switching this off puts your own theme back")
    look_layout: BoolProperty(
        name="ZBrush layout", default=True, update=_look_part,
        description="A ZBrush Layout button in the top bar that opens one big canvas with the Sculpt Kit tray beside it")
    look_shelf: BoolProperty(
        name="ZBrush shelf", default=True, update=_look_part,
        description="Lay the Sculpt Mode brush bar out like ZBrush's shelves, with ZBrush's names, "
                    "and add its view switches to the viewport header")
    look_subtools: BoolProperty(
        name="Subtool list", default=True, update=_look_part,
        description="A Subtools panel in the Sculpt Kit tab that lists the scene's meshes")
    look_material: BoolProperty(
        name="Material", default=True, update=_look_part,
        description="Start new sculpts and the ZBrush layout in the material picked below")
    # Each style remembers its own material.
    look_material_zbrush: EnumProperty(
        name="Material", items=LOOK_MATERIALS, default='RED_WAX', update=_look_style,
        description="What new sculpts and the ZBrush layout start in while the style is ZBrush")
    look_material_klay: EnumProperty(
        name="Material", items=LOOK_MATERIALS, default='BROWN_CLAY', update=_look_style,
        description="What new sculpts and the ZBrush layout start in while the style is KlaySculpt")
    # What the colour theme replaced, as JSON, for putting it back.
    look_theme_backup: StringProperty(options={'HIDDEN'})

    def draw(self, _context):
        from .features import FEATURES, zbrush_keys, zbrush_look
        options = {"zbrush_keys": zbrush_keys.CHEAT_SHEET, "zbrush_look": zbrush_look.OPTIONS}
        layout = self.layout
        for feature in FEATURES:
            box = layout.box()
            box.prop(self, "use_" + feature.id, text=feature.label)
            col = box.column()
            col.active = getattr(self, "use_" + feature.id)
            col.label(text=feature.description)
            if not col.active:
                continue
            if feature.id == "remesh":
                col.prop(self, "remesh_canvas_gesture")
            if feature.id == "zbrush_look":
                col.row().prop(self, "look_style", expand=True)
            for prop_name, lines in options.get(feature.id, ()):
                sub = col.column(align=True)
                sub.prop(self, prop_name)
                hints = sub.column(align=True)
                hints.active = getattr(self, prop_name)
                if prop_name == "look_material":
                    hints.row().prop(self, zbrush_look.material.choice_property(self.look_style), expand=True)
                for line in lines:
                    hints.label(text=line, icon='BLANK1')


def register():
    bpy.utils.register_class(SculptKitPreferences)


def unregister():
    bpy.utils.unregister_class(SculptKitPreferences)
