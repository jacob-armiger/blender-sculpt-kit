"""New sculpt: a dense, evenly quadded sphere or rounded cube, in Sculpt Mode, in a clean viewport.

Either shape is the size of Blender's starting cube: 2 units across. What is
said of the sphere below goes for the cube as well.

The first one goes to the centre of the scene. Blender's starting cube, when it
is the only mesh on screen and untouched, is hidden so that the sphere takes its
place. The view moves only if it does not already show the sphere at a workable
size.

One added while a mesh is on screen goes on the surface in the middle of the
view, and the view is left where it is.
"""
import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty
from bpy_extras import view3d_utils
from mathutils import Vector

from .. import common

# The view is left alone when it shows the new sphere between these sizes, as fractions of the viewport's shorter side.
SMALLEST, LARGEST = 0.05, 1.0

# A cube subdivided n times has 6 * 4^n faces.
DENSITIES = (
    ('6', "25 Thousand Faces", "Light: for blocking out big forms"),
    ('7', "100 Thousand Faces", "Medium"),
    ('8', "400 Thousand Faces", "Dense: room for secondary forms"),
    ('9', "1.5 Million Faces", "Very dense: fine detail, slower to create"),
)


SHAPES = (
    ('SPHERE', "Sphere", "A sphere of quads, all about the same size"),
    ('CUBE', "Cube", "A cube with rounded edges and corners, of quads all about the same size"),
)


def _quad_cube(scene, levels, radius, rounding):
    """A cube subdivided `levels` times into a flat grid on every side, with its edges and corners rounded off.

    `rounding` is the radius of the rounding as a share of half the cube's width: 0 leaves a plain cube,
    1 makes a sphere. `radius` is half the cube's width."""
    import bmesh
    cage = bpy.data.meshes.new("SculptKit Cage")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(cage)
    bm.free()

    holder = bpy.data.objects.new("SculptKit Cage", cage)
    scene.collection.objects.link(holder)
    subdivide = holder.modifiers.new("Subdivide", 'SUBSURF')
    subdivide.subdivision_type = 'SIMPLE'  # keeps the sides flat: every point stays on the cube
    subdivide.levels = levels
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(holder.evaluated_get(depsgraph))
    bpy.data.objects.remove(holder)
    bpy.data.meshes.remove(cage)

    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    if rounding > 0.0:
        # Rounding an edge shortens the way round it: a band `rounding` wide becomes an arc of pi/4 times that.
        # So the grid lines are first moved towards the edges, by as much as keeps them evenly spaced afterwards.
        flat = 1.0 - rounding
        along = np.abs(co) * (flat + rounding * np.pi / 4.0)
        co = np.sign(co) * np.where(along <= flat, along, flat + (along - flat) / (np.pi / 4.0))
        # Each point is held to a smaller cube inside, then pushed `rounding` out from it. Along an edge that
        # sweeps a quarter circle. The tangent spaces the points evenly round it, where a straight push would
        # crowd them towards the middle of the edge.
        core = np.clip(co, -flat, flat)
        out = rounding * np.tan((co - core) / rounding * (np.pi / 4.0))
        co = core + rounding * out / np.linalg.norm(out, axis=1)[:, None]
    mesh.vertices.foreach_set("co", (co * radius).ravel())
    mesh.update()
    mesh.shade_smooth()
    return mesh


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


def _viewport(context):
    """The 3D viewport the operator runs in, or None."""
    space = context.space_data
    return space if space is not None and space.type == 'VIEW_3D' else None


def _on_screen(context):
    """The meshes that can be seen: in the viewport the operator runs in, or else in the view layer."""
    space = _viewport(context)
    depsgraph = context.evaluated_depsgraph_get()
    meshes = [ob for ob in context.view_layer.objects if ob.type == 'MESH' and ob.visible_get()]
    if space is None:
        return meshes
    # A viewport can hide more: Solo, and the reference view's halves, each show only some of the scene.
    return [ob for ob in meshes if ob.evaluated_get(depsgraph).visible_in_viewport_get(space)]


def _is_starting_cube(ob):
    """Whether this is Blender's starting cube, untouched: 2 units across, at the centre of the scene."""
    mesh = ob.data
    if len(mesh.vertices) != 8 or len(mesh.polygons) != 6 or len(ob.modifiers):
        return False
    co = np.empty(24, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    return bool(np.allclose(np.abs(co), 1.0, atol=1e-6)
                and np.allclose(np.array(ob.matrix_world), np.identity(4), atol=1e-6))


def _in_view(context, location, radius):
    """Whether the viewport already shows a sphere there, whole enough and large enough to work on."""
    region, rv3d = common.window_region(context.area), context.space_data.region_3d
    centre = view3d_utils.location_3d_to_region_2d(region, rv3d, location)
    edge = view3d_utils.location_3d_to_region_2d(region, rv3d, location + rv3d.view_rotation @ Vector((radius, 0.0, 0.0)))
    if centre is None or edge is None:  # behind the view
        return False
    across = 2.0 * (edge - centre).length / min(region.width, region.height)
    return 0.0 <= centre.x <= region.width and 0.0 <= centre.y <= region.height and SMALLEST <= across <= LARGEST


def _looking_at(context, shown):
    """The point in the middle of the viewport: on the surface that is there, or else as deep as the view's pivot."""
    region, rv3d = common.window_region(context.area), context.space_data.region_3d
    middle = (region.width / 2, region.height / 2)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, middle)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, middle)
    depsgraph = context.evaluated_depsgraph_get()
    start = origin
    for _ in range(32):  # past whatever is in the way but not shown in this viewport
        hit, location, _normal, _face, ob, _matrix = context.scene.ray_cast(depsgraph, start, direction)
        if not hit:
            break
        if ob.original in shown:
            return location
        start = location + direction * max(1e-4, 1e-5 * (location - origin).length)
    return origin + direction * (rv3d.view_location - origin).dot(direction)


class SCULPTKIT_OT_new_sculpt(bpy.types.Operator):
    """Add a dense quad sphere or rounded cube the size of Blender's starting cube and enter Sculpt Mode on it.
One added to a sculpt goes where you are looking, and the view stays put"""
    bl_idname = "sculptkit.new_sculpt"
    bl_label = "New Sculpt"
    bl_options = {'REGISTER', 'UNDO'}

    # Location and framing are worked out afresh each time, unless they are given.
    shape: EnumProperty(name="Shape", items=SHAPES, default='SPHERE', options={'SKIP_SAVE'})
    density: EnumProperty(name="Density", items=DENSITIES, default='8')
    radius: FloatProperty(name="Radius", default=1.0, min=0.001, subtype='DISTANCE', options={'SKIP_SAVE'},
                          description="Half the width of the shape")
    rounding: FloatProperty(name="Rounding", default=0.25, min=0.0, max=1.0, subtype='FACTOR',
                            description="Cube only: how far its edges and corners are rounded off, "
                                        "from a sharp cube at 0 to a sphere at 1")
    location: FloatVectorProperty(name="Location", subtype='TRANSLATION', options={'SKIP_SAVE'})
    frame: BoolProperty(name="Frame It", default=True, options={'SKIP_SAVE'},
                        description="Move the view to the new sphere")
    hide_cube: BoolProperty(name="Hide Starting Cube", default=True,
                            description="When Blender's starting cube is the only mesh on screen, "
                                        "hide it and put the sphere in its place")
    setup_view: BoolProperty(name="Set Up Viewport", default=True,
                             description="Matcap shading with cavity; hide the floor grid, axes and 3D cursor")

    def execute(self, context):
        from .zbrush_look import subtools
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        space = _viewport(context)
        given = self.properties.is_property_set

        shown = _on_screen(context)
        if self.hide_cube and len(shown) == 1 and _is_starting_cube(shown[0]):
            shown.pop().hide_set(True)
        if shown:
            if not given("location") and space is not None:
                self.location = _looking_at(context, shown)
            if not given("frame"):
                self.frame = False
        elif not given("frame") and space is not None:
            self.frame = not _in_view(context, Vector(self.location), self.radius)

        if self.shape == 'CUBE':
            mesh = _quad_cube(context.scene, int(self.density), self.radius, self.rounding)
        else:
            mesh = _quad_sphere(context.scene, int(self.density), self.radius)
        mesh.name = "Sculpt"
        ob = bpy.data.objects.new("Sculpt", mesh)
        ob.location = self.location
        context.collection.objects.link(ob)
        for other in context.selected_objects:
            other.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        subtools.settle(context.view_layer)  # the sculpt this one takes over from may have its eye off

        if space is not None:
            if space.local_view is not None:
                ob.local_view_set(space, True)  # Solo is on: the new sphere joins what it shows
            if self.setup_view:
                setup_view(space)
            if self.frame:
                with context.temp_override(area=context.area, region=common.window_region(context.area)):
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
            col.label(text=label)
            row = col.row(align=True)
            for shape, name, icon in (('SPHERE', "Sphere", 'MESH_UVSPHERE'), ('CUBE', "Cube", 'MESH_CUBE')):
                button = row.operator(SCULPTKIT_OT_new_sculpt.bl_idname, text=name, icon=icon)
                button.shape = shape
                button.density = value


_classes = (SCULPTKIT_OT_new_sculpt, SCULPTKIT_PT_sculpt_session)


def enable():
    for cls in _classes:
        bpy.utils.register_class(cls)


def disable():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
