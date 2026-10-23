"""Remesh tools.

Voxel Remesh works like ZBrush's DynaMesh: one resolution number that follows
the model's size, so the same setting gives the same density whether the model
is a centimetre or ten metres across. Blender's own voxel size is an absolute
length that has to be re-tuned whenever the scale changes.

Repairing Remesh wraps Blender's QuadriFlow quad remesher, which refuses any mesh with an
edge shorter than 0.0001 units and reports it as "needs to be manifold". Fine
voxel meshes hit that limit in two ways: a handful of near-zero edges that the
voxel remesher leaves behind, or simply being small and dense. Repairing Remesh
merges the first kind, scales the mesh up for the duration to avoid the
second, repairs real non-manifold edges, and scales back afterwards.

On a sculpt QuadriFlow also takes its edge directions from brush-level noise,
which is what blows the result out at large reductions. So it is given a copy
smoothed to the size of the quads it is asked for, the way Maxon advises for
ZRemesher, and the quads it makes are then snapped back onto the original sculpt
and relaxed, which brings back the detail they can hold. A result with holes is
retried once with another seed. The seed is in the operator's corner panel: a
new one is a fresh try from the original sculpt.
"""
import bmesh
import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .. import common
from . import remesh_instant

ENGINES = (
    ('QUADRIFLOW', "QuadriFlow", "Blender's quad remesher, with the repairs and smoothing of Repairing Remesh"),
    ('INSTANT', "Instant Meshes", "The Instant Meshes remesher (an experiment): seconds on any mesh, "
                                  "quads from a quarter-count quad-dominant result, no seed and no symmetry"),
)

QUADRIFLOW_EDGE_LIMIT = 1e-4  # object.quadriflow_remesh rejects meshes with an edge shorter than this
SAFE_EDGE = 1e-3              # what the shortest edge is scaled up to before calling it
MAX_SMOOTHING = 40            # Taubin passes at most: about 4 s on a million faces

_added = []  # (keymap, keymap item) pairs this feature created


# Reading a mesh through its attributes is 20 to 60 times faster than through vertices, edges and loops.

def _coordinates(mesh):
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.attributes["position"].data.foreach_get("vector", co)
    return co.reshape(-1, 3)


def _set_coordinates(mesh, co):
    mesh.attributes["position"].data.foreach_set("vector", np.ascontiguousarray(co, dtype=np.float32).ravel())
    mesh.update()


def _edges(mesh):
    verts = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.attributes[".edge_verts"].data.foreach_get("value", verts)
    return verts.reshape(-1, 2)


def _corners(mesh, name):
    values = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.attributes[name].data.foreach_get("value", values)
    return values


def _scale_mesh(mesh, factor):
    if factor != 1.0:
        _set_coordinates(mesh, _coordinates(mesh) * factor)


def _neighbour_mean(co, edges, count):
    """Each vertex's average neighbour position."""
    total = np.empty_like(co)
    for axis in range(3):
        total[:, axis] = (np.bincount(edges[:, 0], weights=co[edges[:, 1], axis], minlength=len(co))
                          + np.bincount(edges[:, 1], weights=co[edges[:, 0], axis], minlength=len(co)))
    return total / np.maximum(count, 1)[:, None]


def _has_multires(ob):
    return any(m.type == 'MULTIRES' for m in ob.modifiers)


def _can_remesh(cls, context):
    ob = context.active_object
    if ob is None or ob.type != 'MESH' or context.mode not in {'OBJECT', 'SCULPT'}:
        return False
    if _has_multires(ob):  # Blender's remeshers refuse a mesh with Multires levels
        cls.poll_message_set("Apply or remove the Multires levels first")
        return False
    return True


# ---------------------------------------------------------------- voxel remesh

class SCULPTKIT_OT_voxel_remesh(bpy.types.Operator):
    """Rebuild the mesh evenly, as voxels: Resolution is how many fit across its longest side (Ctrl R)"""
    bl_idname = "sculptkit.voxel_remesh"
    bl_label = "Voxel Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    resolution: IntProperty(name="Resolution", default=0, min=0, max=4096,
                            description="Voxels along the model's longest side. 0 uses the value in the Sculpt Kit panel")
    keep_attributes: BoolProperty(name="Keep Mask and Face Sets", default=True)

    @classmethod
    def poll(cls, context):
        return _can_remesh(cls, context)

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


# Faces a voxel remesh gives per (surface area / voxel size squared): measured on a sphere, a subdivided
# Suzanne and a thin cylinder (1.3 to 1.5); flat-sided shapes give about 1.0. Sculpts are mostly curved.
FACES_PER_VOXEL_AREA = 1.4
_measured = {}  # mesh pointer -> (vertex count, face count, when, surface area, longest side)


def _measure(mesh):
    """The mesh's surface area and longest side. On a dense sculpt reading every face takes a tenth of a second
    and the panel is drawn often, so they are worked out again only when the mesh gains or loses faces (a remesh,
    Divide, Dyntopo), and then no more than every few seconds. Sculpting alone changes them too little to matter."""
    import time
    key, now = mesh.as_pointer(), time.monotonic()
    known = _measured.get(key)
    if known and (known[:2] == (len(mesh.vertices), len(mesh.polygons)) or now - known[2] < 3.0):
        return known[3:]
    area = np.empty(len(mesh.polygons), dtype=np.float32)
    mesh.polygons.foreach_get("area", area)
    co = _coordinates(mesh)
    measured = (float(area.sum()), float(np.ptp(co, axis=0).max()) if len(co) else 0.0)
    _measured[key] = (len(mesh.vertices), len(mesh.polygons), now) + measured
    return measured


def estimate_faces(mesh, resolution):
    """About how many faces Voxel Remesh would give at this resolution, or None."""
    area, longest_side = _measure(mesh)
    if longest_side <= 0.0 or resolution <= 0:
        return None
    return round(FACES_PER_VOXEL_AREA * area / (longest_side / resolution) ** 2)


# ----------------------------------------------------------------- quad remesh

def _inspect(mesh):
    """Per edge: its length, how many faces use it, and whether those faces disagree about which way is out."""
    co = _coordinates(mesh).astype(np.float64)
    edge_verts = _edges(mesh)
    corner_edges = _corners(mesh, ".corner_edge")
    corner_verts = _corners(mesh, ".corner_vert")

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


def _smooth_for_field(mesh, target_faces, strength):
    """Smooth away detail finer than the quads to come, so QuadriFlow sets its directions from the forms.

    Taubin smoothing (a shrinking step, then an inflating one) keeps thin parts such as fingers from shrinking.
    How far it reaches grows with the target quad size: a diffusion spreads about one edge per square root
    of its steps, so the steps are the square of how many input edges fit across one target quad."""
    if strength <= 0.0 or not len(mesh.polygons):
        return
    co = _coordinates(mesh).astype(np.float64)
    edges = _edges(mesh)
    count = np.bincount(edges.ravel(), minlength=len(co))
    edge = float(np.median(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)))
    area = np.empty(len(mesh.polygons), dtype=np.float32)
    mesh.polygons.foreach_get("area", area)
    quad = (float(area.sum()) / max(target_faces, 1)) ** 0.5
    steps = int(min(MAX_SMOOTHING, strength * (quad / max(edge, 1e-12)) ** 2))
    for _ in range(steps):
        co += 0.5 * (_neighbour_mean(co, edges, count) - co)
        co -= 0.53 * (_neighbour_mean(co, edges, count) - co)
    _set_coordinates(mesh, co)


def _project_and_relax(mesh, surface, rounds=2):
    """Snap the new quads onto the original sculpt, evening them out along the surface between snaps."""
    co = _coordinates(mesh).astype(np.float64)
    edges = _edges(mesh)
    count = np.bincount(edges.ravel(), minlength=len(co))

    def snap(points):
        out = np.empty_like(points)
        normals = np.empty_like(points)
        for i, point in enumerate(points):
            hit, normal, _index, _distance = surface.find_nearest(Vector(point))
            out[i] = hit if hit is not None else point
            normals[i] = normal if normal is not None else (0.0, 0.0, 1.0)
        return out, normals

    co, normals = snap(co)
    for _ in range(rounds):
        move = _neighbour_mean(co, edges, count) - co
        move -= (move * normals).sum(axis=1)[:, None] * normals  # only along the surface
        co, normals = snap(co + 0.5 * move)
    _set_coordinates(mesh, co)


def _unfold(mesh, surface, rounds=20):
    """Find the faces where QuadriFlow's grid did not close (folded, tilted far from the sculpt, squashed to
    nothing, or drawn out into needles that radiate from one point, all showing as small pinches) and relax
    the vertices around them along the surface, snapping back onto the sculpt each round, until the quads
    there even out. Returns how many faces were found."""
    count_faces = len(mesh.polygons)
    if not count_faces:
        return 0
    co = _coordinates(mesh).astype(np.float64)
    corners = _corners(mesh, ".corner_vert")
    starts = np.empty(count_faces, dtype=np.int32)
    sizes = np.empty(count_faces, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", sizes)
    normals = np.empty(count_faces * 3, dtype=np.float32)
    centres = np.empty(count_faces * 3, dtype=np.float32)
    areas = np.empty(count_faces, dtype=np.float32)
    mesh.polygons.foreach_get("normal", normals)
    mesh.polygons.foreach_get("center", centres)
    mesh.polygons.foreach_get("area", areas)
    normals, centres = normals.reshape(-1, 3), centres.reshape(-1, 3)

    bad = areas < 0.1 * float(np.median(areas))  # squashed to nothing
    quads = np.flatnonzero(sizes == 4)
    if len(quads):  # needles: the longest side four times the shortest
        ring = corners[starts[quads][:, None] + np.arange(4)]
        sides = np.linalg.norm(co[ring] - co[np.roll(ring, -1, axis=1)], axis=2)
        bad[quads] |= sides.max(axis=1) > 4.0 * np.maximum(sides.min(axis=1), 1e-12)
    for i, centre in enumerate(centres):  # folded over, or tilted more than 60 degrees from the sculpt
        _hit, normal, _index, _distance = surface.find_nearest(Vector(centre))
        if normal is not None and normals[i] @ np.asarray(normal) <= 0.5:
            bad[i] = True
    found = np.flatnonzero(bad)
    if not len(found):
        return 0

    picked = np.zeros(len(co), dtype=bool)
    for face in found:
        picked[corners[starts[face]:starts[face] + sizes[face]]] = True
    edges = _edges(mesh)
    for _ in range(3):  # three rings of neighbours, so the fix blends into the surface around it
        grown = picked.copy()
        grown[edges[picked[edges[:, 0]], 1]] = True
        grown[edges[picked[edges[:, 1]], 0]] = True
        picked = grown
    moving = np.flatnonzero(picked)
    count = np.bincount(edges.ravel(), minlength=len(co))
    for _ in range(rounds):
        target = co[moving] + 0.5 * (_neighbour_mean(co, edges, count)[moving] - co[moving])
        for k, point in zip(moving, target):
            hit, _normal, _index, _distance = surface.find_nearest(Vector(point))
            co[k] = hit if hit is not None else point
    _set_coordinates(mesh, co)
    return len(found)


def _repair_pinches(mesh, surface, rounds=10):
    """Rebuild the faces around each vertex where six or more edges meet: the vertex is dissolved, which
    leaves one face with as many sides as it had edges, that face is cut into quads by chords from its
    lowest-valence corner (and one triangle when the count is odd), and the corners are relaxed along the
    surface and snapped back onto it. A singularity split in the sense of Peng et al. 2011 ("Connectivity
    editing for quadrilateral meshes"): poles of valence 4 and 5 in place of one of 6 or more. Returns how
    many were rebuilt."""
    rebuilt = 0
    for _ in range(3):  # poles next to each other are taken one at a time, a pass each
        before = _pinch_points(mesh)
        if not before:
            break
        bm = bmesh.new()
        bm.from_mesh(mesh)
        poles = [v for v in bm.verts if len(v.link_edges) >= 6]
        chosen, taken = [], set()
        for pole in poles:  # no two in one pass share a face, so each n-gon comes from one pole
            if not any(f.index in taken for f in pole.link_faces):
                chosen.append(pole)
                taken.update(f.index for f in pole.link_faces)
        ring = {v.index for pole in chosen for e in pole.link_edges for v in e.verts}
        bmesh.ops.dissolve_verts(bm, verts=chosen)
        for face in [f for f in bm.faces if len(f.verts) > 4]:
            while face is not None and len(face.verts) > 4:
                # One chord at a time, always from the corner with the fewest edges, so the new valence spreads
                # instead of piling onto one corner.
                corners = face.verts[:]
                start = min(range(len(corners)), key=lambda i: len(corners[i].link_edges))
                corners = corners[start:] + corners[:start]
                cut = bmesh.ops.connect_verts(bm, verts=[corners[0], corners[3]])
                face = next((f for e in cut["edges"] for f in e.link_faces if len(f.verts) > 4), None)
        tried = mesh.copy()
        bm.to_mesh(tried)
        bm.free()
        tried.update()
        if _pinch_points(tried) >= before:  # it did not help: keep what we had
            bpy.data.meshes.remove(tried)
            break
        # Relax the corners around each rebuilt pole along the surface, as _unfold does for pinched faces.
        co = _coordinates(tried).astype(np.float64)
        edges = _edges(tried)
        count = np.bincount(edges.ravel(), minlength=len(co))
        moving = np.array(sorted(i for i in ring if i < len(co)), dtype=np.int64)
        for _ in range(rounds):
            target = co[moving] + 0.5 * (_neighbour_mean(co, edges, count)[moving] - co[moving])
            for k, point in zip(moving, target):
                hit, _normal, _index, _distance = surface.find_nearest(Vector(point))
                co[k] = hit if hit is not None else point
        _set_coordinates(tried, co)
        # Hand the rebuilt geometry back to the mesh the operator is working on.
        bm = bmesh.new()
        bm.from_mesh(tried)
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
        bpy.data.meshes.remove(tried)
        rebuilt += len(chosen)
    return rebuilt


def _pinch_points(mesh):
    """Vertices where six or more edges meet: where QuadriFlow's grid could not close, which another seed
    usually moves away."""
    if not len(mesh.edges):
        return 0
    edges = _edges(mesh)
    return int((np.bincount(edges.ravel(), minlength=len(mesh.vertices)) >= 6).sum())


def _open_edges(mesh):
    if not len(mesh.edges):
        return 0
    uses = np.bincount(_corners(mesh, ".corner_edge"), minlength=len(mesh.edges))
    return int((uses == 1).sum())


class SCULPTKIT_OT_quad_remesh(bpy.types.Operator):
    """Rebuild the mesh as clean quads. Repairs what Blender's quad remesher would refuse, remeshes a smoothed
copy so the result follows the forms, then snaps it back onto the sculpt"""
    bl_idname = "sculptkit.quad_remesh"
    bl_label = "Repairing Remesh"
    bl_options = {'REGISTER', 'UNDO'}

    target_faces: IntProperty(name="Faces", default=0, min=0, soft_max=500_000,
                              description="Roughly how many faces to aim for. 0 uses the value in the Sculpt Kit panel")
    seed: IntProperty(name="Seed", default=0, min=0,
                      description="Change it for a different layout: each value is a fresh try from the original sculpt")
    smoothing: FloatProperty(name="Smoothing", default=1.0, min=0.0, soft_max=2.0,
                             description="How much finer-than-quad detail is smoothed away before the layout is "
                                         "worked out. The detail comes back when the quads are snapped onto the sculpt")
    use_symmetry: BoolProperty(name="Use Mesh Symmetry", default=True,
                               description="Build a symmetrical result using the mesh's symmetry settings")
    preserve_sharp: BoolProperty(name="Preserve Sharp Edges", default=False, options={'SKIP_SAVE'},
                                 description="Keep sharp creases. Left unset, the Sculpt Kit panel's checkbox decides")
    smooth_normals: BoolProperty(name="Smooth Normals", default=True)
    repair_pinches: BoolProperty(name="Rebuild Pinch Points", default=True,
                                 description="Cut the faces around each vertex where six or more edges meet and "
                                             "refill them with quads snapped to the sculpt")
    engine: EnumProperty(name="Engine", items=ENGINES, default='QUADRIFLOW', options={'SKIP_SAVE'},
                         description="Which remesher lays out the quads. Left unset, the Sculpt Kit panel decides")

    @classmethod
    def poll(cls, context):
        return _can_remesh(cls, context)

    def execute(self, context):
        ob = context.active_object
        # Values left unset come from the Sculpt Kit panel. They are written back, so that the corner panel
        # shows what was used, and a change there (a new Seed) reruns with the same settings.
        if not self.target_faces:
            self.target_faces = context.scene.sculpt_kit_quad_faces
        if not self.properties.is_property_set("preserve_sharp"):
            self.preserve_sharp = context.scene.sculpt_kit_quad_sharp
        if not self.properties.is_property_set("engine"):
            self.engine = context.scene.sculpt_kit_quad_engine
        target, sharp = self.target_faces, self.preserve_sharp
        if self.engine == 'INSTANT' and not remesh_instant.available():
            self.report({'ERROR'}, "Instant Meshes is not installed for this platform")
            return {'CANCELLED'}
        with common.object_mode(context):
            original = ob.data
            surface = BVHTree.FromObject(ob, context.evaluated_depsgraph_get())  # the sculpt, to snap back onto
            closed = _open_edges(original) == 0
            result, repairs, holes, unfolded, rebuilt, pinches = {'CANCELLED'}, {}, False, 0, 0, 0
            for attempt in range(2):  # a result with holes gets one more try with another seed
                work = original.copy()
                ob.data = work
                _smooth_for_field(work, target, self.smoothing * (1.5 if attempt else 1.0))
                scale, repairs = prepare_for_quadriflow(work)
                try:
                    if self.engine == 'INSTANT':
                        remesh_instant.remesh(work, target, pure_quad=True, crease_angle=50.0 if sharp else -1.0,
                                              fill_holes=closed)
                        result = {'FINISHED'}
                    else:
                        result = bpy.ops.object.quadriflow_remesh(
                            mode='FACES', target_faces=target, use_mesh_symmetry=self.use_symmetry,
                            use_preserve_sharp=sharp, smooth_normals=self.smooth_normals,
                            preserve_attributes=True, seed=self.seed + attempt)
                except RuntimeError:  # QuadriFlow reports a refusal as an error, not as a cancelled result
                    result = {'CANCELLED'}
                finally:
                    _scale_mesh(work, 1.0 / scale)
                holes = 'FINISHED' in result and closed and _open_edges(work) > 0
                if 'FINISHED' in result and (not holes or attempt == 1):
                    break
                ob.data = original
                bpy.data.meshes.remove(work)
                work = None
            if work is not None:
                _project_and_relax(work, surface)
                unfolded = _unfold(work, surface)
                rebuilt = _repair_pinches(work, surface) if self.repair_pinches else 0
                pinches = _pinch_points(work)
                name = original.name
                if original.users == 0:
                    bpy.data.meshes.remove(original)
                work.name = name

        repaired = ", ".join("%d %s" % (count, what) for what, count in repairs.items())
        if work is None:
            self.report({'ERROR'}, "Quad remesh failed" + (" even after repairs (%s)" % repaired if repaired else "")
                        + ". Try another Seed in the corner panel, or more Smoothing")
            return {'CANCELLED'}
        self.report({'WARNING'} if holes or pinches else {'INFO'}, "Quad remeshed to {:,} faces".format(len(work.polygons))
                    + (". Repaired first: " + repaired if repaired else "")
                    + (". Evened out %d pinched faces" % unfolded if unfolded else "")
                    + (". Rebuilt %d pinch point%s" % (rebuilt, "s" if rebuilt > 1 else "") if rebuilt else "")
                    + (". It has holes" if holes else "")
                    + (". %d pinch point%s" % (pinches, "s" if pinches > 1 else "") if pinches else "")
                    + (": try another Seed in the corner panel" if holes or pinches else ""))
        return {'FINISHED'}


# ------------------------------------------------------------------- interface

class SCULPTKIT_PT_remesh(bpy.types.Panel):
    bl_label = "Remesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = common.PANEL_CATEGORY
    bl_order = 4

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'

    def draw(self, context):
        scene, ob = context.scene, context.active_object
        now = len(ob.data.polygons)
        col = self.layout.column()
        blocked = _has_multires(ob)
        if blocked:
            common.info(col, "Apply or remove Multires first", icon='INFO')

        row = col.row(align=True).split(factor=0.68, align=True)
        row.enabled = not blocked
        row.prop(scene, "sculpt_kit_remesh_resolution", text="Voxel")
        row.operator(SCULPTKIT_OT_voxel_remesh.bl_idname, text="Remesh")
        faces = None if blocked else estimate_faces(ob.data, scene.sculpt_kit_remesh_resolution)
        if faces is not None:
            common.info(col, "%s \u2192 about %s faces" % (common.face_count(now), common.face_count(faces)))
            if faces < 0.5 * now:
                common.warning(col, "Loses fine detail")

        col.separator()
        row = col.row(align=True).split(factor=0.68, align=True)
        row.enabled = not blocked
        row.prop(scene, "sculpt_kit_quad_faces", text="Repairing", slider=True)  # a drag across covers the usual range
        row.operator(SCULPTKIT_OT_quad_remesh.bl_idname, text="Remesh")
        sub = col.row()
        sub.enabled = not blocked
        sub.prop(scene, "sculpt_kit_quad_sharp")
        if remesh_instant.available():  # the experiment's engine switch shows only where its wheel is installed
            sub = col.row()
            sub.enabled = not blocked
            sub.prop(scene, "sculpt_kit_quad_engine", text="Engine")
        if not blocked:
            common.info(col, "%s \u2192 about %s faces" % (common.face_count(now),
                                                            common.face_count(scene.sculpt_kit_quad_faces)))


_classes = (SCULPTKIT_OT_voxel_remesh, SCULPTKIT_OT_quad_remesh, SCULPTKIT_PT_remesh)


def enable():
    bpy.types.Scene.sculpt_kit_remesh_resolution = IntProperty(
        name="Resolution", default=128, min=8, soft_max=1024, max=4096,
        description="How many voxels fit across the model's longest side. Higher keeps finer detail; doubling it "
                    "gives about four times the faces. The same value gives the same density on a model of any size")
    bpy.types.Scene.sculpt_kit_quad_sharp = BoolProperty(
        name="Preserve Sharp Edges", default=True,
        description="Keep sharp creases in a Repairing Remesh. On dense sculpts it also keeps the result from "
                    "distorting where the surface turns sharply")
    bpy.types.Scene.sculpt_kit_quad_faces = IntProperty(
        name="Quad Faces", default=5000, min=100, soft_max=100_000, max=2_000_000,
        description="Roughly how many faces Repairing Remesh aims for. Drag across for up to 100 thousand, "
                    "Shift + drag for finer steps, or type a number for more")
    bpy.types.Scene.sculpt_kit_quad_engine = EnumProperty(
        name="Engine", items=ENGINES, default='QUADRIFLOW',
        description="Which remesher Repairing Remesh lays its quads out with")
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
    _measured.clear()
    del bpy.types.Scene.sculpt_kit_remesh_resolution
    del bpy.types.Scene.sculpt_kit_quad_faces
    del bpy.types.Scene.sculpt_kit_quad_sharp
    del bpy.types.Scene.sculpt_kit_quad_engine
