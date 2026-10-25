"""Remesh tools.

Voxel Remesh works like ZBrush's DynaMesh: one resolution number that follows
the model's size, so the same setting gives the same density whether the model
is a centimetre or ten metres across. Blender's own voxel size is an absolute
length that has to be re-tuned whenever the scale changes.

fRemesher ("free mesher") is the kit's quad remesh. It lays out edge loops that follow the sculpt's forms
(remesh_field.py: a curvature-aligned direction field worked out on a simplified copy), has the kit's native
engine (native/, loaded in remesh_instant.py) build the quads on that field, and snaps them back onto the
original sculpt. Where the native engine cannot load it falls back, with a note in its report, to Blender's
own QuadriFlow, as below.

The QuadriFlow path wraps Blender's QuadriFlow quad remesher, which refuses any mesh with an
edge shorter than 0.0001 units and reports it as "needs to be manifold". Fine
voxel meshes hit that limit in two ways: a handful of near-zero edges that the
voxel remesher leaves behind, or simply being small and dense. The path
merges the first kind, scales the mesh up for the duration to avoid the
second, repairs real non-manifold edges, and scales back afterwards.

On a sculpt QuadriFlow also takes its edge directions from brush-level noise,
which is what blows the result out at large reductions. So it is given a copy
smoothed to the size of the quads it is asked for, the way Maxon advises for
ZRemesher, and the quads it makes are then snapped back onto the original sculpt
and relaxed, which brings back the detail they can hold. A result with holes is
retried once with another seed. The seed is in the operator's corner panel: a
new one is a fresh try from the original sculpt.

Faces and Adaptive Size in the operator's corner panel are typed-only text fields (Blender's number fields
always allow dragging, and every tick of a drag would rerun the remesh): see _typed_number.
"""
import math
import re

import bmesh
import bpy
import numpy as np
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .. import common
from . import remesh_field, remesh_instant

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


def _relax_graph(mesh):
    """Who each vertex is averaged with when smoothing or relaxing: its neighbours, except that a vertex on an
    open edge takes only its neighbours along open edges, so it slides along the edge instead of being pulled
    in off it. Returns the graph (receiver, giver, count) and the mask of vertices on open edges."""
    edges = _edges(mesh)
    uses = np.bincount(_corners(mesh, ".corner_edge"), minlength=len(edges))
    open_edges = uses == 1
    on_rim = np.zeros(len(mesh.vertices), dtype=bool)
    on_rim[edges[open_edges].ravel()] = True
    receiver = np.concatenate([edges[:, 0], edges[:, 1]])
    giver = np.concatenate([edges[:, 1], edges[:, 0]])
    keep = ~on_rim[receiver] | np.concatenate([open_edges, open_edges])
    receiver, giver = receiver[keep], giver[keep]
    return (receiver, giver, np.bincount(receiver, minlength=len(mesh.vertices))), on_rim


def _graph_mean(co, graph):
    """Each vertex's average over the vertices the graph gives it."""
    receiver, giver, count = graph
    total = np.empty_like(co)
    for axis in range(3):
        total[:, axis] = np.bincount(receiver, weights=co[giver, axis], minlength=len(co))
    return total / np.maximum(count, 1)[:, None]


class _Sculpt:
    """The original sculpt that a remesh is snapped back onto: its surface, and, when it has open edges, those
    edges (as their vertices and midpoints, dense on a sculpt), so the new open edges land on the old ones
    rather than anywhere on the surface near them."""

    def __init__(self, ob, depsgraph):
        from mathutils.kdtree import KDTree
        self.surface = BVHTree.FromObject(ob, depsgraph)
        self.rim = None
        mesh = ob.data
        if len(mesh.edges):
            edges = _edges(mesh)
            open_edges = edges[np.bincount(_corners(mesh, ".corner_edge"), minlength=len(edges)) == 1]
            if len(open_edges):
                co = _coordinates(mesh)
                points = np.concatenate([co[open_edges[:, 0]], 0.5 * (co[open_edges[:, 0]] + co[open_edges[:, 1]])])
                self.rim = KDTree(len(points))
                for i, point in enumerate(points):
                    self.rim.insert(point, i)
                self.rim.balance()


    def find_nearest(self, point):
        return self.surface.find_nearest(point)

    def snap(self, points, on_rim=None, directions=None, reach=0.0):
        """The point on the sculpt for each point, and the sculpt's normal there. With directions (normals of
        the mesh being snapped) and a reach, along that line, the nearer hit either way within the reach: in
        a narrow crevice the nearest point can lie on the far wall, so neighbours snapped to nearest points
        would land on opposite walls and fold. Only a hit on surface facing the same way counts (its normal
        within about 80 degrees of the direction): across a narrow space, between the legs or under an arm,
        the nearer surface along the line is often the other side, and snapping there webs the two together.
        Without such a hit the nearest point, if it faces the same way, else the point stays where it is. For
        points on open edges (on_rim: onto the sculpt's own open edges), the nearest point."""
        out = np.empty_like(points)
        normals = np.empty_like(points)
        for i, point in enumerate(points):
            p = Vector(point)
            if on_rim is not None and self.rim is not None and on_rim[i]:
                p = self.rim.find(p)[0]
            elif directions is not None:
                d = Vector(directions[i])
                ahead = self.surface.ray_cast(p, d, reach)
                behind = self.surface.ray_cast(p, -d, reach)
                hits = [h for h in (ahead, behind) if h[0] is not None and h[1].dot(d) > 0.2]
                if hits:
                    hit = min(hits, key=lambda h: h[3])
                    out[i] = hit[0]
                    normals[i] = hit[1]
                    continue
                hit, normal, _index, _distance = self.surface.find_nearest(p)
                if hit is None or normal.dot(d) <= 0.2:  # only the other side of a gap is near: stay
                    out[i] = point
                    normals[i] = directions[i]
                    continue
                out[i] = hit
                normals[i] = normal
                continue
            hit, normal, _index, _distance = self.surface.find_nearest(p)
            out[i] = hit if hit is not None else p
            normals[i] = normal if normal is not None else (0.0, 0.0, 1.0)
        return out, normals


def _unhide_result(ob):
    """Switch off, in the viewport only, the Decimate modifiers that would show a remesh as a scatter of
    triangles (scanned casts carry one to stay light while they are dense). They also have to be off before
    the sculpt is read for snapping, or it is read decimated. Returns the modifiers switched off."""
    hiding = [m for m in ob.modifiers if m.type == 'DECIMATE' and m.show_viewport]
    for m in hiding:
        m.show_viewport = False
    return hiding


def _shown_note(hidden):
    count = len(hidden)
    return (". Turned off the Decimate modifier in the viewport so the new mesh shows as it is" if count == 1 else
            ". Turned off %d Decimate modifiers in the viewport so the new mesh shows as it is" % count if count else "")


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
        self.report({'INFO'}, "Remeshed to {:,} faces".format(len(context.active_object.data.polygons))
                    + _shown_note(_unhide_result(context.active_object)))
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
    graph, _on_rim = _relax_graph(mesh)
    edge = float(np.median(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)))
    area = np.empty(len(mesh.polygons), dtype=np.float32)
    mesh.polygons.foreach_get("area", area)
    quad = (float(area.sum()) / max(target_faces, 1)) ** 0.5
    steps = int(min(MAX_SMOOTHING, strength * (quad / max(edge, 1e-12)) ** 2))
    for _ in range(steps):
        co += 0.5 * (_graph_mean(co, graph) - co)
        co -= 0.53 * (_graph_mean(co, graph) - co)
    _set_coordinates(mesh, co)


def _fill_stray_holes(mesh, sculpt):
    """Fill the holes a remesh of an open sculpt leaves where the sculpt itself has none: loops of open edges
    that do not run along the sculpt's own open edges."""
    if sculpt.rim is None or not len(mesh.edges):
        return
    co = _coordinates(mesh)
    edges = _edges(mesh)
    tolerance = 1.5 * float(np.median(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)))
    remesh_instant._fill_holes(mesh, keep=lambda verts: np.median(
        [sculpt.rim.find(v.co)[2] for v in verts]) <= tolerance)


def _snap_lines(mesh, co):
    """For snapping a remesh onto the sculpt along its own normals: the normals, and how far to look (a
    little over one edge)."""
    edges = _edges(mesh)
    directions = np.empty(len(co) * 3, dtype=np.float32)
    mesh.vertex_normals.foreach_get("vector", directions)
    return directions.reshape(-1, 3), 1.5 * float(np.median(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)))


def _project_and_relax(mesh, sculpt, rounds=2):
    """Snap the new quads onto the original sculpt (open edges onto its open edges), evening them out along
    the surface between snaps."""
    co = _coordinates(mesh).astype(np.float64)
    graph, on_rim = _relax_graph(mesh)
    directions, reach = _snap_lines(mesh, co)
    co, normals = sculpt.snap(co, on_rim, directions, reach)
    for _ in range(rounds):
        move = _graph_mean(co, graph) - co
        move -= (move * normals).sum(axis=1)[:, None] * normals  # only along the surface
        co, normals = sculpt.snap(co + 0.5 * move, on_rim, normals, reach)
    _set_coordinates(mesh, co)


def _snap_to_ridges(mesh, sculpt, ridges, reach=1.0, parallel=0.7):
    """Move stretches of the quads' loops onto the sculpt's ridge and valley lines (remesh_field.ridge_lines),
    so the cage folds along them as ZRemesher's does. A vertex within `reach` quads of a ridge point, with an
    edge running along the line there (cosine over `parallel`), is a candidate; candidates joined by such edges
    form stretches of loop; each ridge point votes for the stretch of its nearest candidate, so of two parallel
    loops beside a ridge only the nearer moves, and a stretch with two or more votes moves onto the line (each
    of its vertices to the ridge points that voted for it, then onto the sculpt). A vertex that merely lies
    near a ridge, with no edge along it (a foot beside a plinth's edge), stays. Returns how many moved."""
    from mathutils.kdtree import KDTree
    points, along = ridges
    co = _coordinates(mesh).astype(np.float64)
    edges = _edges(mesh)
    if not len(edges) or not len(points):
        return 0
    lengths = np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)
    quad = float(np.median(lengths))
    unit = (co[edges[:, 1]] - co[edges[:, 0]]) / np.maximum(lengths, 1e-20)[:, None]
    ridge_tree = KDTree(len(points))
    for i, p in enumerate(points):
        ridge_tree.insert(p, i)
    ridge_tree.balance()
    nearest = np.full(len(co), -1)
    for v, p in enumerate(co):
        _q, k, d = ridge_tree.find(Vector(p))
        if d < reach * quad:
            nearest[v] = k
    # an edge between two vertices near the ridge, running along it: part of a stretch of loop on it
    both = (nearest[edges[:, 0]] >= 0) & (nearest[edges[:, 1]] >= 0)
    on_line = np.zeros(len(edges), dtype=bool)
    on_line[both] = np.abs((unit[both] * along[nearest[edges[both, 0]]]).sum(1)) > parallel
    root = np.arange(len(co))

    def find(v):
        while root[v] != v:
            root[v] = root[root[v]]
            v = root[v]
        return v
    for x, y in edges[on_line].tolist():
        rx, ry = find(x), find(y)
        if rx != ry:
            root[rx] = ry
    candidate = np.zeros(len(co), dtype=bool)
    candidate[edges[on_line].ravel()] = True
    stretch = np.array([find(v) for v in range(len(co))])
    picked = np.flatnonzero(candidate)
    if not len(picked):
        return 0
    tree = KDTree(len(picked))
    for n, v in enumerate(picked):
        tree.insert(co[v], n)
    tree.balance()
    votes = {}
    total = np.zeros_like(co)
    count = np.zeros(len(co))
    for p in points:
        _q, n, d = tree.find(Vector(p))
        if d < reach * quad:
            v = picked[n]
            votes[stretch[v]] = votes.get(stretch[v], 0) + 1
            total[v] += p
            count[v] += 1
    moving = np.flatnonzero((count > 0) & np.array([votes.get(stretch[v], 0) >= 2 for v in range(len(co))]))
    if len(moving):
        target, _normals = sculpt.snap(total[moving] / count[moving][:, None])
        co[moving] = target
        _set_coordinates(mesh, co)
    return len(moving)


FIT_SMOOTHING = 8  # rounds of smoothing of the fit's moves (_fit_to_subdivision)


def _fit_to_subdivision(mesh, sculpt, rounds=6, smoothing=FIT_SMOOTHING):
    """Move the cage so its Catmull-Clark limit surface, what subdividing and sculpting on it gives, lies on the
    sculpt, rather than the cage's own vertices: a subdivided cage shrinks inside convex forms, so vertices
    put on the sculpt lose the form once subdivided. A vertex of valence n on quads has its limit point at
    (n^2 v + 4 * sum of its edge neighbours + sum of its diagonal neighbours) / (n (n + 5)); each round takes
    the limit points, their nearest points on the sculpt, and moves each vertex by its limit point's miss (a
    Jacobi step on the least-squares fit, convergent since the limit mask only averages; subdivision-surface
    fitting as in Litke et al.). The moves are smoothed over the cage's neighbours `smoothing` times before
    they are made: the shrinkage they make up for is broad, and chasing the sculpt's detail finer than the
    quads through the limit mask pushes single vertices out into spikes. Vertices on open edges, or on a face
    that is not a quad, stay. Returns the mean move, as a share of the median edge."""
    co = _coordinates(mesh).astype(np.float64)
    edges = _edges(mesh)
    sizes = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", sizes)
    corners = _corners(mesh, ".corner_vert")
    if not len(edges) or not (sizes == 4).any():
        return 0.0
    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    quads = corners[starts[sizes == 4][:, None] + np.arange(4)]
    n = np.bincount(edges.ravel(), minlength=len(co)).astype(np.float64)
    movable = n >= 3
    uses = np.bincount(_corners(mesh, ".corner_edge"), minlength=len(edges))
    movable[edges[uses == 1].ravel()] = False
    others = corners[np.repeat(sizes != 4, sizes)]
    movable[others] = False
    edge_length = float(np.median(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)))

    def limit(p):
        around = np.zeros_like(p)
        diagonal = np.zeros_like(p)
        for axis in range(3):
            around[:, axis] = (np.bincount(edges[:, 0], weights=p[edges[:, 1], axis], minlength=len(p))
                               + np.bincount(edges[:, 1], weights=p[edges[:, 0], axis], minlength=len(p)))
            for k in range(4):
                diagonal[:, axis] += np.bincount(quads[:, k], weights=p[quads[:, (k + 2) % 4], axis], minlength=len(p))
        w = np.maximum(n * (n + 5), 1.0)[:, None]
        return (n[:, None] ** 2 * p + 4.0 * around + diagonal) / w
    count = np.maximum(n, 1.0)[:, None]

    def neighbour_mean(v):
        out = np.zeros_like(v)
        for axis in range(3):
            out[:, axis] = (np.bincount(edges[:, 0], weights=v[edges[:, 1], axis], minlength=len(v))
                            + np.bincount(edges[:, 1], weights=v[edges[:, 0], axis], minlength=len(v)))
        return out / count
    start = co.copy()
    for _ in range(rounds):
        at = limit(co)
        move = np.zeros_like(co)
        move[movable] = sculpt.snap(at[movable])[0] - at[movable]
        for _ in range(smoothing):
            move = 0.5 * (move + neighbour_mean(move))
        co[movable] += move[movable]
    _set_coordinates(mesh, co)
    return float(np.linalg.norm(co - start, axis=1)[movable].mean()) / max(edge_length, 1e-12)


def _bad_faces(mesh, co, sculpt):
    """Faces where the grid did not close or the snap went wrong: folded, tilted more than 60 degrees from
    the sculpt, squashed to nothing, or drawn out into needles that radiate from one point (all showing as
    small pinches). Returns their indices, and the mesh's corner layout (corners, starts, sizes)."""
    count_faces = len(mesh.polygons)
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
        _hit, normal, _index, _distance = sculpt.find_nearest(Vector(centre))
        if normal is not None and normals[i] @ np.asarray(normal) <= 0.5:
            bad[i] = True
    return np.flatnonzero(bad), (corners, starts, sizes)


def _around(mesh, faces, layout, rings):
    """The vertices of these faces and `rings` rings of their neighbours, as a mask."""
    corners, starts, sizes = layout
    picked = np.zeros(len(mesh.vertices), dtype=bool)
    for face in faces:
        picked[corners[starts[face]:starts[face] + sizes[face]]] = True
    edges = _edges(mesh)
    for _ in range(rings):
        grown = picked.copy()
        grown[edges[picked[edges[:, 0]], 1]] = True
        grown[edges[picked[edges[:, 1]], 0]] = True
        picked = grown
    return picked


def _unfold(mesh, sculpt, rounds=20, coarse=None):
    """Even out the bad faces (_bad_faces): relax the vertices around them along the surface, snapping back
    onto the sculpt each round. Where that leaves faces still bad, the sculpt has detail finer than the
    quads there (fingers spread on hair, at a low count), which they cannot follow: with `coarse` (a BVH of
    the surface at quad scale, the smoothed proxy), those vertices are relaxed onto it instead, so the patch
    keeps the large form rather than tearing between the fine ones. Returns how many faces were found."""
    if not len(mesh.polygons):
        return 0
    co = _coordinates(mesh).astype(np.float64)
    found, layout = _bad_faces(mesh, co, sculpt)
    if not len(found):
        return 0
    moving = np.flatnonzero(_around(mesh, found, layout, 3))  # three rings: the fix blends into the surface
    graph, on_rim = _relax_graph(mesh)
    directions, reach = _snap_lines(mesh, co)
    for _ in range(rounds):
        target = co[moving] + 0.5 * (_graph_mean(co, graph)[moving] - co[moving])
        co[moving], directions[moving] = sculpt.snap(target, on_rim[moving], directions[moving], reach)
    _set_coordinates(mesh, co)
    if coarse is not None:
        still, layout = _bad_faces(mesh, co, sculpt)
        if len(still):
            moving = np.flatnonzero(_around(mesh, still, layout, 2) & ~on_rim)
            for _ in range(rounds):
                target = co[moving] + 0.5 * (_graph_mean(co, graph)[moving] - co[moving])
                for k, point in zip(moving, target):
                    hit = coarse.find_nearest(Vector(point))[0]
                    co[k] = hit if hit is not None else point
            _set_coordinates(mesh, co)
    return len(found)


def _repair_pinches(mesh, sculpt, rounds=10):
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
        graph, on_rim = _relax_graph(tried)
        directions, reach = _snap_lines(tried, co)
        moving = np.array(sorted(i for i in ring if i < len(co)), dtype=np.int64)
        for _ in range(rounds):
            target = co[moving] + 0.5 * (_graph_mean(co, graph)[moving] - co[moving])
            co[moving], directions[moving] = sculpt.snap(target, on_rim[moving], directions[moving], reach)
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


FACES_MIN, FACES_MAX = 100, 2_000_000  # the range of the Faces setting, typed or set by a script


def adaptive_for(faces):
    """Adaptive Size's default for a face count: 1 up to 3,000 faces, easing (with the log of the count) to 0.75
    at 10,000 and over. On the bench (24 Oct 2026) 1 misses the sculpt 10 to 20% less than 0.75 at 1k, while at
    10k 0.75 gives squarer, more even quads and fewer poles at about the same fidelity."""
    t = (math.log(max(faces, 1)) - math.log(3_000)) / (math.log(10_000) - math.log(3_000))
    return 1.0 - 0.25 * min(max(t, 0.0), 1.0)


last_report = ("", "")  # the last fRemesher report (type, text), for the tests: a run's reports are kept nowhere


def _report(operator, kind, text):
    global last_report
    last_report = (kind, text)
    operator.report({kind}, text)


# Said wherever QuadriFlow stands in for the native engine: its results are clearly worse (on the bench 1.5 to
# 3.5 times the misses, and some casts fail outright), so it is a warning, not a note
FALLBACK = "fRemesher's engine couldn't be loaded, so Blender's QuadriFlow is used instead (lower quality)"


def _field_engine():
    """Whether fRemesher's engine (the kit's native module) loaded on this platform."""
    return remesh_instant.sculpt_kit_remesh is not None


def parse_number(text):
    """The number typed into a text field, or None when it is not one. Accepts 5000, 5,000, 5 000, 1.5k, 2m
    and, for fractions, 50%. A comma counts only as a thousands separator."""
    t = text.strip().lower().replace(" ", "").replace("_", "")
    if "," in t:
        if not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d*)?[km%]?", t):
            return None
        t = t.replace(",", "")
    scale = 1.0
    if t[-1:] in ("k", "m", "%"):
        scale, t = {"k": 1e3, "m": 1e6, "%": 0.01}[t[-1]], t[:-1]
    try:
        value = float(t) * scale
    except ValueError:
        return None
    return value if math.isfinite(value) else None


class SCULPTKIT_OT_quad_remesh(bpy.types.Operator):
    """fRemesher: rebuild the mesh as clean quads whose edge loops follow the forms of the sculpt, then snap them
back onto it"""
    bl_idname = "sculptkit.quad_remesh"
    bl_label = "fRemesher"
    bl_options = {'REGISTER', 'UNDO'}

    # Faces and Seed start afresh on every run (SKIP_SAVE): remembered, the panel's count would be ignored
    # from the second run on. The corner panel still shows and reruns with the values used. The same goes for
    # every setting that is left unset to follow the Sculpt Kit panel.
    # The corner panel draws Faces and Adaptive Size as text fields (target_faces_text, adaptive_size_text), which
    # execute() parses and writes back to the numbers; the numbers stay for scripts and are never drawn there.
    target_faces: IntProperty(name="Faces", default=0, min=0, max=FACES_MAX, options={'SKIP_SAVE'},
                              description="Roughly how many faces to aim for. 0 uses the value in the Sculpt Kit panel")
    target_faces_text: StringProperty(name="Faces", options={'SKIP_SAVE'},
                                      description="Roughly how many faces to aim for. Type a number (5000, 5,000 or "
                                                  "5k) and press Enter; the remesh runs again then, not while you type")
    seed: IntProperty(name="Seed", default=0, min=0, options={'SKIP_SAVE'},
                      description="Change it for a different layout: each value is a fresh try from the original sculpt")
    smoothing: FloatProperty(name="Smoothing", default=1.0, min=0.0, soft_max=2.0,
                             description="How much finer-than-quad detail is smoothed away before the layout is "
                                         "worked out. The detail comes back when the quads are snapped onto the sculpt")
    use_symmetry: BoolProperty(name="Use Mesh Symmetry", default=True, options={'SKIP_SAVE'},
                               description="Build a symmetrical result using the mesh's symmetry settings")
    preserve_sharp: BoolProperty(name="Preserve Sharp Edges", default=False, options={'SKIP_SAVE'},
                                 description="Keep sharp creases (only where fRemesher's engine is not built and "
                                             "QuadriFlow is used instead; fRemesher has Detect Edges). Left unset, "
                                             "the Sculpt Kit panel's checkbox decides")
    smooth_normals: BoolProperty(name="Smooth Normals", default=False, options={'SKIP_SAVE'},
                                 description="Shade the result smooth. Off, it shades flat, as ZBrush shows a cage")
    repair_pinches: BoolProperty(name="Rebuild Pinch Points", default=True, options={'SKIP_SAVE'},
                                 description="Cut the faces around each vertex where six or more edges meet and "
                                             "refill them with quads snapped to the sculpt")
    follow_forms: FloatProperty(name="Follow Forms", default=1.0, min=0.0, soft_max=10.0,
                                description="How strongly edge loops follow the curvature. "
                                            "0 gives the smoothest layout, ignoring the forms")
    # Left unset it comes from the panel, which follows Faces unless set by hand (adaptive_for); adaptive_auto
    # says so, and a new count typed in the corner panel then gets its own default too
    adaptive_size: FloatProperty(name="Adaptive Size", default=1.0, min=0.0, max=1.0, options={'SKIP_SAVE'},
                                 description="Quads short across curves and long along them, and smaller on thin "
                                             "parts such as fingers, for the same face count. Lower face counts "
                                             "work best with more adaptive sizing. 0 to 1")
    adaptive_size_text: StringProperty(name="Adaptive Size", options={'SKIP_SAVE'},
                                       description="Quads short across curves and long along them, and smaller on "
                                                   "thin parts such as fingers. Lower face counts work best with more "
                                                   "adaptive sizing. Type a number from 0 to 1 and press Enter; the "
                                                   "remesh runs again then, not while you type")
    adaptive_auto: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    # Off by default, in Advanced: it lowers the subdivided cage's misses 5 to 15% on the bench, too little to see
    # at a figure's scale (Jacob, 24 Oct 2026), and leaves the cage itself 1 to 3 degrees rougher
    fit_subdivision: BoolProperty(name="Fit for Subdivision", default=False, options={'SKIP_SAVE'},
                                  description="Place the cage so that subdividing it, as "
                                              "Multires does, lands on the sculpt; the cage itself sits just off it")
    follow_ridges: BoolProperty(name="Follow Ridges", default=False, options={'SKIP_SAVE'},
                                description="Run edge loops along the sculpt's ridges and "
                                            "valleys, such as a nose, brow or lips, so flat shading shows the forms")
    detect_edges: BoolProperty(name="Detect Edges", default=True, options={'SKIP_SAVE'},
                               description="Lay edge loops along the sculpt's hard edges, such as "
                                           "a plinth's or an armour plate's, so they stay crisp")

    @classmethod
    def poll(cls, context):
        return _can_remesh(cls, context)

    def draw(self, context):
        # Everything on the surface, in groups: this panel is already the place for tuning a result. Labels on
        # the left and fields on the right (property split), checkbox groups under a heading, a gap between groups
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        col.prop(self, "target_faces_text")
        if not _field_engine():  # QuadriFlow stands in: the settings it has
            col = layout.column(heading="Shape")
            col.prop(self, "preserve_sharp")
            col.prop(self, "use_symmetry")
            layout.separator()
            col = layout.column()
            col.prop(self, "smoothing")
            col.prop(self, "repair_pinches")
            col.prop(self, "smooth_normals")
            col.prop(self, "seed")
            return
        col.prop(self, "adaptive_size_text")
        layout.separator()
        col = layout.column()
        col.prop(self, "follow_forms")
        col = layout.column(heading="Loops")
        col.prop(self, "detect_edges")
        col.prop(self, "follow_ridges")
        col.prop(self, "use_symmetry")
        layout.separator()
        col = layout.column()
        col.prop(self, "smoothing")
        col = layout.column(heading="Surface")
        col.prop(self, "fit_subdivision")
        col.prop(self, "repair_pinches")
        col.prop(self, "smooth_normals")
        layout.separator()
        layout.prop(self, "seed")

    def _typed(self, name, low, high, whole, notes):
        """The setting `name` (target_faces or adaptive_size) from the text field mirroring it, if that holds
        something new: parsed and clamped to low..high. Text that is not a number keeps the last value (what the
        number holds, from the run before) and adds a note for the report. Writes the value used back to both."""
        text_name = name + "_text"
        value = getattr(self, name)
        text = getattr(self, text_name).strip()
        label = self.properties.bl_rna.properties[name].name
        shown = "{:,}".format if whole else "{:.2f}".format
        if self.properties.is_property_set(text_name) and text:
            typed = parse_number(text)
            if typed is None:
                notes.append('%s: "%s" is not a number, kept %s' % (label, text, shown(value)))
            else:
                value = min(max(typed, low), high)
                if value != typed:
                    notes.append("%s: %s is outside %s to %s, used %s" % (label, text, shown(low), shown(high),
                                                                          shown(round(value) if whole else value)))
                value = round(value) if whole else value
        setattr(self, name, value)
        setattr(self, text_name, shown(value))
        return value

    def execute(self, context):
        ob = context.active_object
        # Values left unset come from the Sculpt Kit panel. They are written back, so that the corner panel
        # shows what was used, and a change there (a new Seed) reruns with the same settings.
        scene, notes = context.scene, []
        if not self.target_faces:
            self.target_faces = scene.sculpt_kit_quad_faces
        if not self.properties.is_property_set("preserve_sharp"):
            self.preserve_sharp = scene.sculpt_kit_quad_sharp
        if not self.properties.is_property_set("adaptive_size"):
            self.adaptive_size = scene.sculpt_kit_quad_adaptive
            self.adaptive_auto = scene.sculpt_kit_quad_adaptive_auto
        if not self.properties.is_property_set("detect_edges"):
            self.detect_edges = scene.sculpt_kit_quad_edges
        if not self.properties.is_property_set("follow_ridges"):
            self.follow_ridges = scene.sculpt_kit_quad_ridges
        for name, setting in (("use_symmetry", "sculpt_kit_quad_symmetry"), ("smooth_normals", "sculpt_kit_quad_smooth"),
                              ("fit_subdivision", "sculpt_kit_quad_fit"), ("repair_pinches", "sculpt_kit_quad_pinches")):
            if not self.properties.is_property_set(name):
                setattr(self, name, getattr(scene, setting))
        target = self._typed("target_faces", FACES_MIN, FACES_MAX, True, notes)
        if self.adaptive_auto:  # following the count, until a value is typed in
            if self.adaptive_size_text.strip() in ("", "{:.2f}".format(self.adaptive_size)):
                self.adaptive_size, self.adaptive_size_text = adaptive_for(target), ""
            else:
                self.adaptive_auto = False
        self._typed("adaptive_size", 0.0, 1.0, False, notes)
        sharp, field_engine = self.preserve_sharp, _field_engine()
        with common.object_mode(context):
            original = ob.data
            shown = _unhide_result(ob)  # before reading the sculpt, which a viewport Decimate would thin out
            surface = _Sculpt(ob, context.evaluated_depsgraph_get())  # the sculpt, to snap back onto
            closed = _open_edges(original) == 0
            result, repairs, holes, unfolded, rebuilt, pinches = {'CANCELLED'}, {}, False, 0, 0, 0
            work, asymmetric, ridges = None, False, None
            if field_engine:
                mirror = next((axis for axis, on in enumerate((original.use_mirror_x, original.use_mirror_y,
                                                               original.use_mirror_z)) if on), None)
                try:
                    work, report, _coarse, ridges = remesh_field.remesh(
                        ob, target, context, smoothing=self.smoothing, align=self.follow_forms,
                        adaptive=self.adaptive_size, seed=self.seed, detect_edges=self.detect_edges,
                        ridges=self.follow_ridges,
                        mirror_axis=mirror if self.use_symmetry else None)
                    ob.data = work
                    if not closed:
                        _fill_stray_holes(work, surface)
                    holes = closed and _open_edges(work) > 0
                    asymmetric = report.get("asymmetric", False)
                except RuntimeError as error:
                    for m in shown:
                        m.show_viewport = True
                    _report(self, 'ERROR', "fRemesher failed: %s" % error)
                    return {'CANCELLED'}
            for attempt in range(0 if work is not None else 2):  # QuadriFlow: holes get one more try with another seed
                work = original.copy()
                ob.data = work
                _smooth_for_field(work, target, self.smoothing * (1.5 if attempt else 1.0))
                scale, repairs = prepare_for_quadriflow(work)
                try:
                    result = bpy.ops.object.quadriflow_remesh(
                        mode='FACES', target_faces=target, use_mesh_symmetry=self.use_symmetry,
                        use_preserve_sharp=sharp, use_preserve_boundary=not closed,
                        smooth_normals=self.smooth_normals,
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
                if ridges is not None:
                    _snap_to_ridges(work, surface, ridges)
                # fRemesher's quads are left as they snap: unfolding pulled patches off the sculpt (onto the
                # smoothed proxy where faces stayed bad), on the Apollo Lykeios at 3k sculpt missed p95 0.62 to
                # 0.88% and folds 10 to 32, and hid webs rather than mending them
                unfolded = 0 if field_engine else _unfold(work, surface)
                rebuilt = _repair_pinches(work, surface) if self.repair_pinches else 0
                pinches = _pinch_points(work)
                if field_engine and self.fit_subdivision:
                    _fit_to_subdivision(work, surface)
                if field_engine:  # Blender's QuadriFlow sets the shading itself (smooth_normals)
                    work.shade_smooth() if self.smooth_normals else work.shade_flat()
                name = original.name
                if original.users == 0:
                    bpy.data.meshes.remove(original)
                work.name = name

        repaired = ", ".join("%d %s" % (count, what) for what, count in repairs.items())
        if work is None:
            for m in shown:
                m.show_viewport = True
            _report(self, 'ERROR', "fRemesher's engine couldn't be loaded, and the QuadriFlow stand-in failed"
                        + (" even after repairs (%s)" % repaired if repaired else "")
                        + ". Try another Seed in the corner panel (Advanced), or more Smoothing")
            return {'CANCELLED'}
        _report(self, 'WARNING' if holes or pinches or notes or not field_engine else 'INFO',
                "fRemesher: {:,} faces".format(len(work.polygons))
                + ("" if field_engine else ". " + FALLBACK)
                + (". Repaired first: " + repaired if repaired else "")
                + (". Evened out %d pinched faces" % unfolded if unfolded else "")
                + (". Rebuilt %d pinch point%s" % (rebuilt, "s" if rebuilt > 1 else "") if rebuilt else "")
                + (". Not mirror-symmetric, so remeshed whole" if asymmetric else "")
                + (". It has holes" if holes else "")
                + (". %d pinch point%s" % (pinches, "s" if pinches > 1 else "") if pinches else "")
                + (": try another Seed in the corner panel (Advanced)" if holes or pinches else "")
                + "".join(". " + note for note in notes)
                + _shown_note(shown))
        return {'FINISHED'}


# ------------------------------------------------------------------- interface

class SCULPTKIT_PT_remesh(bpy.types.Panel):
    """Remesh: Voxel Remesh and fRemesher as sub-panels, each with its own header, so a note under one (the
    voxel estimate, its warning) is plainly about that one."""
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
        if _has_multires(context.active_object):
            common.info(self.layout.column(), "Apply or remove Multires first", icon='INFO')


class _RemeshPart:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = common.PANEL_CATEGORY
    bl_parent_id = "SCULPTKIT_PT_remesh"

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'


def _action_row(layout, enabled):
    """A setting and its button on one line, the button a fixed third of the width, as in Blender's own
    Remesh panel."""
    row = layout.row(align=True).split(factor=0.66, align=True)
    row.enabled = enabled
    return row


class SCULPTKIT_PT_remesh_voxel(_RemeshPart, bpy.types.Panel):
    bl_label = "Voxel Remesh"

    def draw(self, context):
        scene, ob = context.scene, context.active_object
        blocked = _has_multires(ob)
        col = self.layout.column()
        row = _action_row(col, not blocked)
        row.prop(scene, "sculpt_kit_remesh_resolution", text="Resolution")
        row.operator(SCULPTKIT_OT_voxel_remesh.bl_idname, text="Remesh")
        faces = None if blocked else estimate_faces(ob.data, scene.sculpt_kit_remesh_resolution)
        if faces is not None:
            now = len(ob.data.polygons)
            common.info(col, "%s → about %s faces" % (common.face_count(now), common.face_count(faces)))
            if faces < 0.5 * now:
                common.warning(col, "Loses fine detail")


class SCULPTKIT_PT_remesh_quads(_RemeshPart, bpy.types.Panel):
    bl_label = "fRemesher"

    def draw(self, context):
        scene, ob = context.scene, context.active_object
        blocked = _has_multires(ob)
        layout = self.layout
        row = _action_row(layout, not blocked)
        row.prop(scene, "sculpt_kit_quad_faces", text="Faces")
        row.operator(SCULPTKIT_OT_quad_remesh.bl_idname, text="Remesh")
        col = layout.column()
        col.enabled = not blocked
        col.use_property_split = True
        col.use_property_decorate = False
        if not _field_engine():  # QuadriFlow stands in, and keeps sharp creases only when asked to
            box = col.box().column(align=True)  # first, and in two lines: a panel label does not wrap
            common.warning(box, "fRemesher's engine couldn't load")
            common.info(box, "QuadriFlow stands in, at lower quality")
            sharp = col.column()
            sharp.use_property_split = False  # its label in full, at the sidebar's width
            sharp.prop(scene, "sculpt_kit_quad_sharp")
            return
        row = col.row(align=True)
        row.prop(scene, "sculpt_kit_quad_adaptive", text="Adaptive Size", slider=True)
        auto = scene.sculpt_kit_quad_adaptive_auto
        row.prop(scene, "sculpt_kit_quad_adaptive_auto", text="", icon='LINKED' if auto else 'UNLINKED')
        loops = col.column(heading="Loops")
        loops.prop(scene, "sculpt_kit_quad_edges")
        loops.prop(scene, "sculpt_kit_quad_ridges")


class SCULPTKIT_PT_remesh_quads_advanced(_RemeshPart, bpy.types.Panel):
    bl_label = "Advanced"
    bl_parent_id = "SCULPTKIT_PT_remesh_quads"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        scene = context.scene
        col = self.layout.column(heading="Result")
        col.use_property_split = True
        col.use_property_decorate = False
        col.enabled = not _has_multires(context.active_object)
        col.prop(scene, "sculpt_kit_quad_symmetry")
        col.prop(scene, "sculpt_kit_quad_fit")
        col.prop(scene, "sculpt_kit_quad_pinches")
        col.prop(scene, "sculpt_kit_quad_smooth")


_classes = (SCULPTKIT_OT_voxel_remesh, SCULPTKIT_OT_quad_remesh, SCULPTKIT_PT_remesh, SCULPTKIT_PT_remesh_voxel,
            SCULPTKIT_PT_remesh_quads, SCULPTKIT_PT_remesh_quads_advanced)


def enable():
    bpy.types.Scene.sculpt_kit_remesh_resolution = IntProperty(
        name="Resolution", default=128, min=8, soft_max=1024, max=4096,
        description="How many voxels fit across the model's longest side. Higher keeps finer detail; doubling it "
                    "gives about four times the faces. The same value gives the same density on a model of any size")
    bpy.types.Scene.sculpt_kit_quad_sharp = BoolProperty(
        name="Preserve Sharp Edges", default=True,
        description="Keep sharp creases when QuadriFlow stands in for fRemesher (fRemesher has Detect Edges). "
                    "On dense sculpts it also keeps the result from distorting where the surface turns sharply")
    bpy.types.Scene.sculpt_kit_quad_faces = IntProperty(
        name="Quad Faces", default=5000, min=FACES_MIN, soft_max=100_000, max=FACES_MAX,
        description="Roughly how many faces fRemesher aims for. Drag across for up to 100 thousand, "
                    "Shift + drag for finer steps, or type a number for more")
    # Adaptive Size follows Faces (adaptive_for) until set by hand; Auto turns that back on
    bpy.types.Scene.sculpt_kit_quad_adaptive_auto = BoolProperty(
        name="Auto", default=True,
        description="Adaptive Size follows Faces: more of it at low face counts, which work best with more "
                    "adaptive sizing")
    bpy.types.Scene.sculpt_kit_quad_adaptive_value = FloatProperty(default=1.0, min=0.0, max=1.0)

    def adaptive_get(scene):
        if scene.sculpt_kit_quad_adaptive_auto:
            return adaptive_for(scene.sculpt_kit_quad_faces)
        return scene.sculpt_kit_quad_adaptive_value

    def adaptive_set(scene, value):
        scene.sculpt_kit_quad_adaptive_value = value
        scene.sculpt_kit_quad_adaptive_auto = False

    bpy.types.Scene.sculpt_kit_quad_adaptive = FloatProperty(
        name="Adaptive Size", default=1.0, min=0.0, max=1.0, get=adaptive_get, set=adaptive_set,
        description="Quads short across curves and long along them, and smaller on thin parts such as fingers, "
                    "for the same face count. Lower face counts work best with more adaptive sizing")
    bpy.types.Scene.sculpt_kit_quad_edges = BoolProperty(
        name="Detect Edges", default=True,
        description="Lay edge loops along the sculpt's hard edges, such as a plinth's or "
                    "an armour plate's, so they stay crisp")
    bpy.types.Scene.sculpt_kit_quad_ridges = BoolProperty(
        name="Follow Ridges", default=False,
        description="Run edge loops along the sculpt's ridges and valleys, such as a nose, "
                    "brow or lips, so flat shading shows the forms")
    bpy.types.Scene.sculpt_kit_quad_symmetry = BoolProperty(
        name="Use Mesh Symmetry", default=True,
        description="Build a symmetrical result using the mesh's symmetry settings")
    bpy.types.Scene.sculpt_kit_quad_fit = BoolProperty(
        name="Fit for Subdivision", default=False,
        description="Place the cage so that subdividing it, as Multires does, lands on the sculpt; the cage itself "
                    "sits just off it")
    bpy.types.Scene.sculpt_kit_quad_pinches = BoolProperty(
        name="Rebuild Pinch Points", default=True,
        description="Cut the faces around each vertex where six or more edges meet and refill them with quads "
                    "snapped to the sculpt")
    bpy.types.Scene.sculpt_kit_quad_smooth = BoolProperty(
        name="Smooth Normals", default=False,
        description="Shade the result smooth. Off, it shades flat, as ZBrush shows a cage")
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
    del bpy.types.Scene.sculpt_kit_quad_adaptive
    del bpy.types.Scene.sculpt_kit_quad_adaptive_value
    del bpy.types.Scene.sculpt_kit_quad_adaptive_auto
    del bpy.types.Scene.sculpt_kit_quad_edges
    del bpy.types.Scene.sculpt_kit_quad_ridges
    del bpy.types.Scene.sculpt_kit_quad_symmetry
    del bpy.types.Scene.sculpt_kit_quad_fit
    del bpy.types.Scene.sculpt_kit_quad_pinches
    del bpy.types.Scene.sculpt_kit_quad_smooth
