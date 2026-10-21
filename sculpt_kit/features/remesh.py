"""Remesh tools.

Voxel Remesh works like ZBrush's DynaMesh: one resolution number that follows
the model's size, so the same setting gives the same density whether the model
is a centimetre or ten metres across. Blender's own voxel size is an absolute
length that has to be re-tuned whenever the scale changes.

Quad Remesh wraps Blender's QuadriFlow remesher, which refuses any mesh with an
edge shorter than 0.0001 units and reports it as "needs to be manifold". Fine
voxel meshes hit that limit in two ways: a handful of near-zero edges that the
voxel remesher leaves behind, or simply being small and dense. Quad Remesh
merges the first kind, scales the mesh up for the duration to avoid the
second, repairs real non-manifold edges, and scales back afterwards.
"""
import bmesh
import bpy
import numpy as np
from bpy.props import BoolProperty, IntProperty

from .. import common

QUADRIFLOW_EDGE_LIMIT = 1e-4  # object.quadriflow_remesh rejects meshes with an edge shorter than this
SAFE_EDGE = 1e-3              # what the shortest edge is scaled up to before calling it

_added = []  # (keymap, keymap item) pairs this feature created


def _coordinates(mesh):
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def _scale_mesh(mesh, factor):
    if factor != 1.0:
        mesh.vertices.foreach_set("co", (_coordinates(mesh) * factor).ravel())
        mesh.update()


# ---------------------------------------------------------------- voxel remesh

class SCULPTKIT_OT_voxel_remesh(bpy.types.Operator):
    """Rebuild the mesh at an even density set by Resolution, the number of voxels along its longest side"""
    bl_idname = "sculptkit.voxel_remesh"
    bl_label = "Voxel Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    resolution: IntProperty(name="Resolution", default=0, min=0, max=4096,
                            description="Voxels along the model's longest side. 0 uses the value in the Sculpt Kit panel")
    keep_attributes: BoolProperty(name="Keep Mask and Face Sets", default=True)

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and context.mode in {'OBJECT', 'SCULPT'}

    def execute(self, context):
        mesh = context.active_object.data
        if not mesh.vertices:
            return {'CANCELLED'}
        longest_side = float(np.ptp(_coordinates(mesh), axis=0).max())
        if longest_side <= 0.0:
            return {'CANCELLED'}
        resolution = self.resolution or context.scene.sculpt_kit_remesh_resolution
        mesh.remesh_voxel_size = longest_side / resolution
        mesh.use_remesh_preserve_attributes = self.keep_attributes
        if 'FINISHED' not in bpy.ops.object.voxel_remesh():
            return {'CANCELLED'}
        self.report({'INFO'}, "Remeshed to {:,} faces".format(len(context.active_object.data.polygons)))
        return {'FINISHED'}


# ----------------------------------------------------------------- quad remesh

def _inspect(mesh):
    """Per edge: its length, how many faces use it, and whether those faces disagree about which way is out."""
    co = _coordinates(mesh).astype(np.float64)
    edge_verts = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edge_verts)
    edge_verts = edge_verts.reshape(-1, 2)
    corner_edges = np.empty(len(mesh.loops), dtype=np.int32)
    corner_verts = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("edge_index", corner_edges)
    mesh.loops.foreach_get("vertex_index", corner_verts)

    lengths = np.linalg.norm(co[edge_verts[:, 0]] - co[edge_verts[:, 1]], axis=1)
    uses = np.bincount(corner_edges, minlength=len(mesh.edges))
    forward = np.bincount(corner_edges, weights=edge_verts[corner_edges, 0] == corner_verts, minlength=len(mesh.edges))
    flipped = (uses == 2) & (forward != 1)
    return lengths, uses, flipped


def _repair(mesh, merge_distance, fix_normals):
    """Merge near-zero edges and cut out real non-manifold geometry. Returns what was done."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    done = {}

    verts_before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=merge_distance)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=merge_distance * 0.01)
    done["tiny edges merged"] = verts_before - len(bm.verts)

    wire = [e for e in bm.edges if not e.link_faces]
    if wire:
        bmesh.ops.delete(bm, geom=wire, context='EDGES')
    loose = [v for v in bm.verts if not v.link_edges]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
    done["loose pieces removed"] = len(wire) + len(loose)

    # An edge shared by more than two faces: remove the faces around it and patch the hole they leave.
    crowded = [e for e in bm.edges if len(e.link_faces) > 2]
    if crowded:
        faces = list({f for e in crowded for f in e.link_faces})
        rim = {e for f in faces for e in f.edges}
        bmesh.ops.delete(bm, geom=faces, context='FACES')
        bmesh.ops.holes_fill(bm, edges=[e for e in rim if e.is_valid and e.is_boundary], sides=0)
    done["non-manifold edges repaired"] = len(crowded)

    if fix_normals or crowded:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        done["normals made consistent"] = 1 if fix_normals else 0

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return {what: count for what, count in done.items() if count}


def prepare_for_quadriflow(mesh):
    """Make the mesh acceptable to QuadriFlow. Returns (scale applied, what was repaired)."""
    lengths, uses, flipped = _inspect(mesh)
    if not len(lengths):
        return 1.0, {}
    # Near-zero edges are judged against the mesh's own fine detail, not a fixed length.
    merge_distance = 0.05 * float(np.percentile(lengths, 5))
    repairs = {}
    needs_repair = bool((lengths < merge_distance).any() or (uses > 2).any() or (uses == 0).any() or flipped.any())
    if needs_repair:
        repairs = _repair(mesh, merge_distance, bool(flipped.any()))
    scale = max(1.0, SAFE_EDGE / merge_distance) if merge_distance > 0.0 else 1.0
    _scale_mesh(mesh, scale)
    return scale, repairs


class SCULPTKIT_OT_quad_remesh(bpy.types.Operator):
    """Rebuild the mesh as clean quads. Repairs what Blender's quad remesher would refuse, then runs it"""
    bl_idname = "sculptkit.quad_remesh"
    bl_label = "Quad Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    target_faces: IntProperty(name="Faces", default=0, min=0, soft_max=500_000,
                              description="Roughly how many faces to aim for. 0 uses the value in the Sculpt Kit panel")
    use_symmetry: BoolProperty(name="Use Mesh Symmetry", default=True,
                               description="Build a symmetrical result using the mesh's symmetry settings")
    preserve_sharp: BoolProperty(name="Preserve Sharp Edges", default=False)
    smooth_normals: BoolProperty(name="Smooth Normals", default=True)

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and context.mode in {'OBJECT', 'SCULPT'}

    def execute(self, context):
        ob = context.active_object
        target = self.target_faces or context.scene.sculpt_kit_quad_faces
        with common.object_mode(context):
            scale, repairs = prepare_for_quadriflow(ob.data)
            try:
                result = bpy.ops.object.quadriflow_remesh(
                    mode='FACES', target_faces=target, use_mesh_symmetry=self.use_symmetry,
                    use_preserve_sharp=self.preserve_sharp, smooth_normals=self.smooth_normals)
            finally:
                _scale_mesh(ob.data, 1.0 / scale)

        repaired = ", ".join("%d %s" % (count, what) for what, count in repairs.items())
        if 'FINISHED' not in result:
            self.report({'ERROR'}, "Quad remesh failed" + (" even after repairs (%s)" % repaired if repaired else ""))
            return {'CANCELLED'}
        self.report({'INFO'}, "Quad remeshed to {:,} faces".format(len(ob.data.polygons))
                    + (". Repaired first: " + repaired if repaired else ""))
        return {'FINISHED'}


# ------------------------------------------------------------------- interface

class SCULPTKIT_PT_remesh(bpy.types.Panel):
    bl_label = "Remesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = common.PANEL_CATEGORY
    bl_order = 4
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'

    def draw(self, context):
        scene = context.scene
        col = self.layout.column()
        col.prop(scene, "sculpt_kit_remesh_resolution")
        col.operator(SCULPTKIT_OT_voxel_remesh.bl_idname, text="Voxel Remesh (Ctrl R)", icon='MOD_REMESH')
        col.separator()
        col.prop(scene, "sculpt_kit_quad_faces", slider=True)  # a drag across it covers the whole usual range
        col.operator(SCULPTKIT_OT_quad_remesh.bl_idname, icon='MESH_GRID')


_classes = (SCULPTKIT_OT_voxel_remesh, SCULPTKIT_OT_quad_remesh, SCULPTKIT_PT_remesh)


def enable():
    bpy.types.Scene.sculpt_kit_remesh_resolution = IntProperty(
        name="Resolution", default=128, min=8, soft_max=1024, max=4096,
        description="Voxels along the model's longest side for Voxel Remesh. Doubling it gives about four times the faces")
    bpy.types.Scene.sculpt_kit_quad_faces = IntProperty(
        name="Quad Faces", default=5000, min=100, soft_max=100_000, max=2_000_000,
        description="Roughly how many faces Quad Remesh aims for. Drag across for up to 100 thousand, "
                    "Shift + drag for finer steps, or type a number for more")
    for cls in _classes:
        bpy.utils.register_class(cls)
    keymap = common.sculpt_keymap()
    if keymap is not None:
        # Takes over Blender's own Ctrl + R remesh, which uses the absolute voxel size.
        _added.append((keymap, keymap.keymap_items.new(SCULPTKIT_OT_voxel_remesh.bl_idname, 'R', 'PRESS', ctrl=True)))


def disable():
    common.remove_keymap_items(_added)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.sculpt_kit_remesh_resolution
    del bpy.types.Scene.sculpt_kit_quad_faces
