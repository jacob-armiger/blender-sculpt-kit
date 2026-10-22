"""New sculpt: an evenly meshed sphere, rounded cube or thin cylinder, in Sculpt Mode, in a clean viewport.

Every shape has about 25 thousand faces' worth of detail (Divide adds more), of
quads or, if picked, triangles. The sphere and cube are the size of Blender's
starting cube: 2 units across. The cylinder is as tall, and thin, with rounded
ends, for laying out an armature for a figure. What is said of the sphere below
goes for the others as well.

The first one goes to the centre of the scene. Blender's starting cube, when it
is the only mesh on screen and untouched, is hidden so that the sphere takes its
place. The view moves only if it does not already show the sphere at a workable
size.

One added while a mesh is on screen goes on the surface in the middle of the
view, and the view is left where it is.
"""
import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, StringProperty
from bpy_extras import view3d_utils
from mathutils import Vector

from .. import common

# The view is left alone when it shows the new sphere between these sizes, as fractions of the viewport's shorter side.
SMALLEST, LARGEST = 0.05, 1.0

# A cube subdivided n times has 6 * 4^n faces: 6 gives about 25 thousand. Divide adds detail from there.
LEVELS = 6
# The cylinder's faces are about the size of the sphere's: its circumference is cut into this many.
CYLINDER_AROUND = 48


SHAPES = (
    ('SPHERE', "Sphere", "A sphere of quads, all about the same size"),
    ('CUBE', "Cube", "A cube with rounded edges and corners, of quads all about the same size"),
    ('CYLINDER', "Cylinder", "A thin upright cylinder with rounded ends, for laying out an armature"),
)


def _box_grid(xs, ys, zs):
    """The surface of a box cut into a grid: lines across it at `xs`, `ys` and `zs`. Quads, facing out."""
    import bmesh
    lines = (xs, ys, zs)
    size = tuple(len(a) - 1 for a in lines)
    index, verts, faces = {}, [], []

    def vertex(key):
        if key not in index:
            index[key] = len(verts)
            verts.append(tuple(lines[a][key[a]] for a in range(3)))
        return index[key]

    for axis in range(3):  # each side of the box: a grid across the other two axes
        b, c = [a for a in range(3) if a != axis]
        for end in (0, size[axis]):
            for u in range(size[b]):
                for v in range(size[c]):
                    corners = []
                    for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        key = [0, 0, 0]
                        key[axis], key[b], key[c] = end, u + du, v + dv
                        corners.append(vertex(tuple(key)))
                    faces.append(corners)
    mesh = bpy.data.meshes.new("Sculpt")
    mesh.from_pydata(verts, [], faces)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def _round_off(mesh, inner, rounding):
    """Hold each point to the box `inner` (half its size on each axis), then push it `rounding` out from there.

    Along an edge that sweeps a quarter circle. The tangent spaces the points evenly round it, where a straight
    push would crowd them towards the middle of the edge."""
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    core = np.clip(co, -inner, inner)
    out = rounding * np.tan((co - core) / rounding * (np.pi / 4.0))
    co = core + rounding * out / np.linalg.norm(out, axis=1)[:, None]
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()
    mesh.shade_smooth()
    return mesh


def _quad_cube(levels, radius, rounding):
    """A cube cut `levels` times into a flat grid on every side, with its edges and corners rounded off.

    `rounding` is the radius of the rounding as a share of half the cube's width: 0 leaves a plain cube,
    1 makes a sphere. `radius` is half the cube's width."""
    lines = np.linspace(-1.0, 1.0, 2 ** levels + 1)
    if rounding > 0.0:
        # Rounding an edge shortens the way round it: a band `rounding` wide becomes an arc of pi/4 times that.
        # So the grid lines are first moved towards the edges, by as much as keeps them evenly spaced afterwards.
        flat = 1.0 - rounding
        along = np.abs(lines) * (flat + rounding * np.pi / 4.0)
        lines = np.sign(lines) * np.where(along <= flat, along, flat + (along - flat) / (np.pi / 4.0))
    lines = lines * radius
    mesh = _box_grid(lines, lines, lines)
    if rounding <= 0.0:
        mesh.shade_smooth()
        return mesh
    return _round_off(mesh, np.full(3, (1.0 - rounding) * radius), rounding * radius)


def _quad_capsule(radius, length, around):
    """An upright cylinder `length` tall with rounded ends of `radius`, of quads all about the same size.

    A box of grid lines with its long sides cut finer, rounded off the way the cube is with a rounding as large
    as the box is thin: across, it becomes a circle, and its ends half spheres."""
    n = around // 4  # grid squares along each side of the box's square cross-section
    half = length / 2.0
    body = max(1, round((length - 2.0 * radius) / (2.0 * np.pi * radius / around)))  # squares along the straight part
    cap = n // 2  # rows of squares from the end of the straight part to the middle of an end
    xs = np.linspace(-radius, radius, n + 1)
    zs = np.concatenate([np.linspace(-half, -half + radius, cap + 1)[:-1],
                         np.linspace(-half + radius, half - radius, body + 1),
                         np.linspace(half - radius, half, cap + 1)[1:]])
    return _round_off(_box_grid(xs, xs, zs), np.array([0.0, 0.0, half - radius]), radius)


def _triangulate(mesh):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces, quad_method='BEAUTY')
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()


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
    """Add a sphere, rounded cube or thin cylinder of about 25 thousand faces and enter Sculpt Mode on it.
One added to a sculpt goes where you are looking, and the view stays put"""
    bl_idname = "sculptkit.new_sculpt"
    bl_label = "New Sculpt"
    bl_options = {'REGISTER', 'UNDO'}

    # Location and framing are worked out afresh each time, unless they are given.
    shape: EnumProperty(name="Shape", items=SHAPES, default='SPHERE', options={'SKIP_SAVE'})
    triangles: BoolProperty(name="Triangles", default=False,
                            description="Made of triangles instead of quads. Divide works best on quads")
    radius: FloatProperty(name="Radius", default=1.0, min=0.001, subtype='DISTANCE', options={'SKIP_SAVE'},
                          description="Half the width of the shape. The cylinder starts at 0.1")
    length: FloatProperty(name="Length", default=2.0, min=0.01, subtype='DISTANCE',
                          description="Cylinder only: its height, rounded ends included")
    # Just enough to take the edges and corners off: a dead sharp edge gives brushes trouble.
    rounding: FloatProperty(name="Rounding", default=0.1, min=0.0, max=1.0, subtype='FACTOR',
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
    made: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # the object the last run of this press added

    def execute(self, context):
        from .zbrush_look import subtools
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        # Changing a setting in the corner panel runs this again, after Blender has undone the run before. When
        # the step before that was a brush stroke, Blender's undo of the run does nothing and the shape it
        # added is still here: every change would add another. So the last one is taken away here if it is.
        stale = bpy.data.objects.get(self.made) if self.made else None
        if stale is not None and stale == context.view_layer.objects.active and stale.type == 'MESH':
            mesh = stale.data
            bpy.data.objects.remove(stale)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        space = _viewport(context)
        given = self.properties.is_property_set

        if self.shape == 'CYLINDER' and not given("radius"):
            self.radius = 0.1
        reach = max(self.radius, self.length / 2.0) if self.shape == 'CYLINDER' else self.radius

        shown = _on_screen(context)
        if self.hide_cube and len(shown) == 1 and _is_starting_cube(shown[0]):
            shown.pop().hide_set(True)
        if shown:
            if not given("location") and space is not None:
                self.location = _looking_at(context, shown)
            if not given("frame"):
                self.frame = False
        elif not given("frame") and space is not None:
            self.frame = not _in_view(context, Vector(self.location), reach)

        if self.shape == 'CYLINDER':
            mesh = _quad_capsule(self.radius, max(self.length, 2.0 * self.radius), CYLINDER_AROUND)
        else:  # the sphere is the cube rounded all the way
            mesh = _quad_cube(LEVELS, self.radius, self.rounding if self.shape == 'CUBE' else 1.0)
        if self.triangles:
            _triangulate(mesh)
        name = next(label for value, label, _description in SHAPES if value == self.shape)  # named after its shape
        mesh.name = name
        ob = bpy.data.objects.new(name, mesh)
        self.made = ob.name
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
    bl_order = 1

    def draw(self, context):
        col = self.layout.column()
        col.row().prop(context.scene, "sculpt_kit_new_faces", expand=True)
        row = col.row(align=True)
        for shape, name, icon in (('SPHERE', "Sphere", 'MESH_UVSPHERE'), ('CUBE', "Cube", 'MESH_CUBE'),
                                  ('CYLINDER', "Cylinder", 'MESH_CYLINDER')):
            button = row.operator(SCULPTKIT_OT_new_sculpt.bl_idname, text=name, icon=icon)
            button.shape = shape
            button.triangles = context.scene.sculpt_kit_new_faces == 'TRIS'


_classes = (SCULPTKIT_OT_new_sculpt, SCULPTKIT_PT_sculpt_session)


def enable():
    bpy.types.Scene.sculpt_kit_new_faces = EnumProperty(
        name="Faces", default='QUADS',
        items=(('QUADS', "Quads", "New shapes are made of quads, which Divide subdivides cleanly"),
               ('TRIS', "Triangles", "New shapes are made of triangles")))
    for cls in _classes:
        bpy.utils.register_class(cls)


def disable():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.sculpt_kit_new_faces
