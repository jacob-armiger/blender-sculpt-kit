"""New sculpt sphere: a dense, evenly quadded sphere, in Sculpt Mode, in a clean viewport."""
import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty

from .. import common

# A cube subdivided n times has 6 * 4^n faces.
DENSITIES = (
    ('6', "25 Thousand Faces", "Light: for blocking out big forms"),
    ('7', "100 Thousand Faces", "Medium"),
    ('8', "400 Thousand Faces", "Dense: room for secondary forms"),
    ('9', "1.5 Million Faces", "Very dense: fine detail, slower to create"),
)


def _quad_sphere(scene, levels, radius):
    """A cube subdivided `levels` times and pushed out to a sphere, so every face is a similar-sized quad."""
    import bmesh
    cage = bpy.data.meshes.new("SculptKit Cage")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(cage)
    bm.free()

    holder = bpy.data.objects.new("SculptKit Cage", cage)
    scene.collection.objects.link(holder)
    subdivide = holder.modifiers.new("Subdivide", 'SUBSURF')
    subdivide.levels = levels
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(holder.evaluated_get(depsgraph))
    bpy.data.objects.remove(holder)
    bpy.data.meshes.remove(cage)

    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    co *= radius / np.linalg.norm(co, axis=1)[:, None]
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()
    mesh.shade_smooth()
    return mesh


def setup_view(space):
    """Solid matcap shading with cavity, and none of the scene clutter that gets in the way of a sculpt."""
    from .zbrush_look import material
    shading, overlay = space.shading, space.overlay
    shading.type = 'SOLID'
    shading.light = 'MATCAP'
    material.apply(shading)  # the ZBrush look's material, when that is switched on
    shading.show_cavity = True
    shading.cavity_type = 'BOTH'
    overlay.show_floor = False
    overlay.show_axis_x = False
    overlay.show_axis_y = False
    overlay.show_cursor = False
    overlay.show_object_origins = False


class SCULPTKIT_OT_new_sculpt(bpy.types.Operator):
    """Add a dense quad sphere, enter Sculpt Mode on it and set the viewport up for sculpting"""
    bl_idname = "sculptkit.new_sculpt"
    bl_label = "New Sculpt Sphere"
    bl_options = {'REGISTER', 'UNDO'}

    density: EnumProperty(name="Density", items=DENSITIES, default='8')
    radius: FloatProperty(name="Radius", default=1.0, min=0.001, subtype='DISTANCE')
    setup_view: BoolProperty(name="Set Up Viewport", default=True,
                             description="Matcap shading with cavity; hide the floor grid, axes and 3D cursor")

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        mesh = _quad_sphere(context.scene, int(self.density), self.radius)
        mesh.name = "Sculpt"
        ob = bpy.data.objects.new("Sculpt", mesh)
        context.collection.objects.link(ob)
        for other in context.selected_objects:
            other.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob

        space = context.space_data
        in_viewport = space is not None and space.type == 'VIEW_3D'
        if in_viewport:
            if self.setup_view:
                setup_view(space)
            if context.region is not None and context.region.type == 'WINDOW':
                bpy.ops.view3d.view_selected()
        bpy.ops.object.mode_set(mode='SCULPT')
        return {'FINISHED'}


class SCULPTKIT_PT_sculpt_session(bpy.types.Panel):
    bl_label = "New Sculpt"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = common.PANEL_CATEGORY
    bl_order = 2

    def draw(self, _context):
        col = self.layout.column()
        for value, label, _description in DENSITIES:
            col.operator(SCULPTKIT_OT_new_sculpt.bl_idname, text="Sphere, " + label, icon='MESH_UVSPHERE').density = value


_classes = (SCULPTKIT_OT_new_sculpt, SCULPTKIT_PT_sculpt_session)


def enable():
    for cls in _classes:
        bpy.utils.register_class(cls)


def disable():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
