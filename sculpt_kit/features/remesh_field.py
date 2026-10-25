"""fRemesher's layout: a cross field aligned to the sculpt's curvature, handed frozen to the native engine's
QuadriFlow back half (its Instant Meshes entry on engines built before that).

The pipeline, on a proxy rather than the sculpt itself:
  1. Proxy: a Voxel Remesh of a copy at a few times the target count (even triangles; detail finer than a
     voxel goes, which the field does not want anyway), or a Decimate of an open mesh.
  2. Smooth it to the target quad size (Taubin), so curvature is read from the
     forms, not from brush strokes.
  3. Principal curvature directions per vertex, and a confidence: how much the two curvatures differ at
     the scale of one quad (none on a sphere or a plane, where any direction will do).
  4. The cross field: the smoothest field that follows the directions where the confidence is high, one
     sparse linear solve with the connection Laplacian of Knoppel et al. 2013, "Globally Optimal Direction
     Fields", by conjugate gradient.
  5. Pole placement: the field's own singularities, minus +1/-1 pairs closer than a few quads (the swirls),
     become prescribed indices of a trivial connection (Crane, Desbrun and Schroder 2010, "Trivial
     Connections on Discrete Surfaces"): the smallest change of the connection that has exactly those
     singularities, whose parallel field is then re-aligned to curvature.
  6. The engine is given the smoothed proxy and the field, frozen: it builds its position field and quads on
     exactly our directions. QuadriFlow's back half by default (its integer flow closes the lattice, so the
     quads have no poles beyond the field's own); Instant Meshes' where the native wheel predates it.
  7. Adaptive Size: two quad sizes per vertex, short across curvature and long along it, capped on thin
     parts, then made consistent with the field (see consistent_sizes) and given to QuadriFlow with it.

Symmetry: the proxy is cut at the mirror plane, one half is remeshed with its quads ending square-on at the
cut, then mirrored and welded.

Everything is numpy with bincount scatters; the matrices are never formed, only applied.
"""
import bmesh
import bpy
import numpy as np

N_SYM = 4  # a cross field: four directions at right angles
# Adaptive Size: how far the sizes may stray from their targets to fit the field (consistent_sizes' weight).
# Smaller fits the field more closely, so QuadriFlow builds more of what it is given, but keeps less of the
# targets: on the Apollo Lykeios at 3k, 0.05 keeps a size spread of 2.5 and QuadriFlow builds 0.64 of it
# (in logs), 0.0005 keeps 1.8 and it builds 0.82.
SIZE_FIT = 0.05
CREASE_ANGLE = 60.0  # degrees: Detect Edges keeps the sculpt's edges folding more than this (sculpt_creases)
# Ridges (ridge_lines): how far the normal must turn across one quad (radians), and the shortest line kept (quads)
RIDGE_TURN = 0.6
RIDGE_LENGTH = 3.0


# --------------------------------------------------------------------------------------------------- proxy

def coordinates(mesh):
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.attributes["position"].data.foreach_get("vector", co)
    return co.reshape(-1, 3)


def triangles(mesh):
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    return tris.reshape(-1, 3)


def proxy_faces(target):
    """How many faces the proxy is made with: enough triangles per quad for the field to place poles between
    them, few enough to keep the solves to seconds."""
    return int(min(max(5 * target, 40_000), 200_000))


def make_proxy(ob, faces, context):
    """A new mesh: the object's own mesh voxel remeshed to about `faces` faces, or decimated when it has open
    edges (a voxel remesh would close them into a shell). Modifiers are ignored."""
    mesh = ob.data.copy()
    mesh.name = ob.data.name + " proxy"
    uses = np.bincount(_corner_edges(mesh), minlength=len(mesh.edges))
    if (uses == 1).any():
        _decimate(mesh, faces)
        return mesh
    area = np.empty(len(mesh.polygons), dtype=np.float32)
    mesh.polygons.foreach_get("area", area)
    temporary = bpy.data.objects.new("Sculpt Kit proxy", mesh)
    context.scene.collection.objects.link(temporary)
    try:
        mesh.remesh_voxel_size = (1.4 * float(area.sum()) / faces) ** 0.5  # remesh.FACES_PER_VOXEL_AREA
        mesh.use_remesh_preserve_attributes = False
        with context.temp_override(object=temporary, active_object=temporary, selected_objects=[temporary]):
            bpy.ops.object.voxel_remesh()
    finally:
        bpy.data.objects.remove(temporary)
    return mesh


def _corner_edges(mesh):
    values = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.attributes[".corner_edge"].data.foreach_get("value", values)
    return values


def _decimate(mesh, faces):
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    ratio = faces / max(1, len(mesh.polygons))  # of the triangles: a quad sculpt has twice as many
    if ratio >= 1.0:
        return
    temporary = bpy.data.objects.new("Sculpt Kit proxy", mesh)
    bpy.context.scene.collection.objects.link(temporary)
    try:
        temporary.modifiers.new("decimate", 'DECIMATE').ratio = ratio
        evaluated = temporary.evaluated_get(bpy.context.evaluated_depsgraph_get())
        decimated = bpy.data.meshes.new_from_object(evaluated)
    finally:
        bpy.data.objects.remove(temporary)
    bm = bmesh.new()
    bm.from_mesh(decimated)
    # Collapsing a thin part can leave two faces on the same three vertices, a closed sliver QuadriFlow crashes
    # on (Blender with it; one on a decimated proxy of the Head of David): both go, and what only they used
    faces = {}
    for face in bm.faces:
        faces.setdefault(frozenset(v.index for v in face.verts), []).append(face)
    doubled = [face for same in faces.values() if len(same) > 1 for face in same]
    if doubled:
        bmesh.ops.delete(bm, geom=doubled, context='FACES')
    bm.to_mesh(mesh)
    bm.free()
    bpy.data.meshes.remove(decimated)


def halve(mesh, axis):
    """Keep the positive side of the mirror plane (bisect, no fill). The cut leaves open edges on the plane."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    normal = [0.0, 0.0, 0.0]
    normal[axis] = 1.0
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-6,
                           plane_co=(0.0, 0.0, 0.0), plane_no=normal, clear_inner=True, clear_outer=False)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def is_symmetric(co, tris, axis, tolerance):
    """Whether the surface is its own mirror image across the plane through the origin: nine in ten of a
    sample of its vertices, mirrored, land within `tolerance` of it. A sculpt with X symmetry switched on but
    posed asymmetrically (a contrapposto) is not, and halving it would make a different statue."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    tree = BVHTree.FromPolygons(co.tolist(), tris.tolist())
    sample = co[np.random.default_rng(0).choice(len(co), size=min(len(co), 2000), replace=False)].copy()
    sample[:, axis] *= -1.0
    gaps = []
    for point in sample:
        hit = tree.find_nearest(Vector(point))
        gaps.append(hit[3] if hit[0] is not None else np.inf)
    return float(np.percentile(gaps, 90)) < tolerance


def mirror_and_weld(mesh, axis, merge_distance):
    """Add the mirror image of the half and weld the two along the plane. Returns how many vertices merged."""
    from mathutils import Matrix
    bm = bmesh.new()
    bm.from_mesh(mesh)
    before = len(bm.verts)
    # The engine leaves the cut's vertices near the plane, not on it: put them on it, so the weld catches all.
    for v in bm.verts:
        if v.is_boundary and abs(v.co[axis]) < 2.0 * merge_distance:
            v.co[axis] = 0.0
    scale = [1.0, 1.0, 1.0]
    scale[axis] = -1.0
    bmesh.ops.mirror(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], matrix=Matrix.Diagonal(scale).to_4x4(),
                     merge_dist=merge_distance, axis="XYZ"[axis])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    merged = 2 * before - len(bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return merged


# ------------------------------------------------------------------------------------------------ geometry

def unique_edges(tris):
    """The mesh's edges (sorted vertex pairs), and the edge of each triangle side (01, 12, 20)."""
    pairs = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    pairs.sort(axis=1)
    edges, inverse = np.unique(pairs, axis=0, return_inverse=True)
    return edges, inverse.reshape(3, -1).T


def taubin(co, edges, target_faces, area, strength, open_edges=None):
    """Smooth away detail finer than the target quads (a shrinking step, then an inflating one, so thin parts
    keep their volume); as many steps as the square of how many edges fit across a quad, at most 40.
    open_edges (a mask over edges): a vertex on them is smoothed along them only, so the edge keeps its place."""
    receiver = np.concatenate([edges[:, 0], edges[:, 1]])
    giver = np.concatenate([edges[:, 1], edges[:, 0]])
    if open_edges is not None and open_edges.any():
        on_rim = np.zeros(len(co), dtype=bool)
        on_rim[edges[open_edges].ravel()] = True
        keep = ~on_rim[receiver] | np.concatenate([open_edges, open_edges])
        receiver, giver = receiver[keep], giver[keep]
    count = np.maximum(np.bincount(receiver, minlength=len(co)), 1)[:, None]
    edge = float(np.median(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)))
    quad = (area / target_faces) ** 0.5
    steps = int(min(40, strength * (quad / max(edge, 1e-12)) ** 2))

    def mean(x):
        total = np.empty_like(x)
        for a in range(3):
            total[:, a] = np.bincount(receiver, weights=x[giver, a], minlength=len(x))
        return total / count
    co = co.copy()
    for _ in range(steps):
        co += 0.5 * (mean(co) - co)
        co -= 0.53 * (mean(co) - co)
    return co, steps


def vertex_normals_and_areas(co, tris):
    """Area-weighted vertex normals, the area each vertex stands for (a third of its triangles') and the total."""
    a, b, c = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    cross = np.cross(b - a, c - a)
    normals = np.zeros_like(co)
    for k in range(3):
        for axis in range(3):
            normals[:, axis] += np.bincount(tris[:, k], weights=cross[:, axis], minlength=len(co))
    areas = np.linalg.norm(cross, axis=1) * 0.5
    vertex_area = np.zeros(len(co))
    for k in range(3):
        vertex_area += np.bincount(tris[:, k], weights=areas / 3.0, minlength=len(co))
    normals /= np.maximum(np.linalg.norm(normals, axis=1), 1e-20)[:, None]
    return normals, vertex_area, float(areas.sum())


def cotan_weights(co, tris, edge_of_side):
    """Per edge, half the sum of the cotangents of the two angles opposite it, kept positive."""
    weights = np.zeros(edge_of_side.max() + 1)
    for side, opposite in ((0, 2), (1, 0), (2, 1)):  # side 01 is opposite corner 2, and so on
        i, j, k = tris[:, side], tris[:, (side + 1) % 3], tris[:, opposite]
        u, v = co[i] - co[k], co[j] - co[k]
        cot = (u * v).sum(1) / np.maximum(np.linalg.norm(np.cross(u, v), axis=1), 1e-20)
        weights += np.bincount(edge_of_side[:, side], weights=cot, minlength=len(weights))
    return np.maximum(weights * 0.5, 1e-4)


def tangent_bases(co, normals, edges):
    """An orthonormal tangent basis (x, y) at each vertex, x along one of its edges projected to the plane."""
    first = np.full(len(co), -1, dtype=np.int64)
    first[edges[:, 0]] = edges[:, 1]
    first[edges[:, 1]] = edges[:, 0]
    x = co[np.maximum(first, 0)] - co
    x -= (x * normals).sum(1)[:, None] * normals
    degenerate = np.linalg.norm(x, axis=1) < 1e-12
    x[degenerate] = np.cross(normals[degenerate], [1.0, 0.0, 0.0])
    x /= np.maximum(np.linalg.norm(x, axis=1), 1e-20)[:, None]
    return x, np.cross(normals, x)


def local_angle(vectors, tx, ty):
    return np.arctan2((vectors * ty).sum(1), (vectors * tx).sum(1))


def principal_directions(co, normals, edges, tx, ty):
    """Per vertex, the angle (in its tangent basis) of the direction of largest curvature, and the two
    principal curvatures: a least-squares shape operator fitted to the normal differences along its edges."""
    n = len(co)
    ata = np.zeros((n, 3))  # the symmetric 2x2 A^T A: xx, xy, yy
    atb = np.zeros((n, 4))  # A^T b for the two rows of the shape operator
    for a, b in ((edges[:, 0], edges[:, 1]), (edges[:, 1], edges[:, 0])):
        dp, dn = co[b] - co[a], normals[b] - normals[a]
        px, py = (dp * tx[a]).sum(1), (dp * ty[a]).sum(1)
        nx, ny = (dn * tx[a]).sum(1), (dn * ty[a]).sum(1)
        for column, values in enumerate((px * px, px * py, py * py)):
            ata[:, column] += np.bincount(a, weights=values, minlength=n)
        for column, values in enumerate((px * nx, py * nx, px * ny, py * ny)):
            atb[:, column] += np.bincount(a, weights=values, minlength=n)
    det = ata[:, 0] * ata[:, 2] - ata[:, 1] ** 2
    det = np.where(np.abs(det) < 1e-20, 1e-20, det)
    inv = np.stack([ata[:, 2], -ata[:, 1], ata[:, 0]], 1) / det[:, None]
    s_xx = inv[:, 0] * atb[:, 0] + inv[:, 1] * atb[:, 1]
    s_xy = inv[:, 1] * atb[:, 0] + inv[:, 2] * atb[:, 1]
    s_yx = inv[:, 0] * atb[:, 2] + inv[:, 1] * atb[:, 3]
    s_yy = inv[:, 1] * atb[:, 2] + inv[:, 2] * atb[:, 3]
    b = 0.5 * (s_xy + s_yx)  # symmetrized
    theta = 0.5 * np.arctan2(2 * b, s_xx - s_yy)
    half_trace = 0.5 * (s_xx + s_yy)
    radius = np.sqrt(((s_xx - s_yy) * 0.5) ** 2 + b ** 2)
    return theta, half_trace + radius, half_trace - radius


def confidence_of(k1, k2, quad):
    """How much a vertex's principal direction means, 0 to 1: the difference of its curvatures as the angle
    the normal turns across one quad, half a radian being full confidence. Zero on spheres and planes."""
    return np.clip(np.abs(k1 - k2) * quad / 0.5, 0.0, 1.0)


def thickness(co, normals, tris, tx, ty, samples=4000):
    """Per vertex, how thick the shape is there: the shape diameter (Shapira, Shamir and Cohen-Or 2008), the
    median length of rays cast inward in a 120 degree cone until they leave through the far side. Measured
    at a sample of the vertices (inf where too few rays come back, as through a half's cut) and spread to
    the rest from the nearest sample."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from mathutils.kdtree import KDTree
    tree = BVHTree.FromPolygons(co.tolist(), tris.tolist())
    reach = float(np.linalg.norm(np.ptp(co, axis=0)))
    k = np.arange(16) + 0.5
    tilt, turn = np.radians(60.0) * np.sqrt(k / 16), k * 2.399963  # a spiral filling the cone evenly
    cone = np.stack([np.sin(tilt) * np.cos(turn), np.sin(tilt) * np.sin(turn), np.cos(tilt)], 1)
    picked = np.random.default_rng(0).choice(len(co), size=min(len(co), samples), replace=False)
    measured = np.full(len(picked), np.inf)
    for n, i in enumerate(picked):
        hits = []
        for c in cone:
            ray = c[0] * tx[i] + c[1] * ty[i] - c[2] * normals[i]
            hit, normal, _face, distance = tree.ray_cast(Vector(co[i] + ray * 1e-6 * reach), Vector(ray), reach)
            if hit is not None and float(np.dot(normal, ray)) > 0.0:
                hits.append(distance)
        if len(hits) >= 4:
            measured[n] = float(np.median(hits))
    kd = KDTree(len(picked))
    for n, i in enumerate(picked):
        kd.insert(co[i], n)
    kd.balance()
    return measured[[kd.find(Vector(p))[1] for p in co]]


def sculpt_creases(co, tris, quad, angle=CREASE_ANGLE, straightness=0.9, min_length=2.0):
    """The sculpt's hard edges, as lines the quads should follow: its own edges that fold more than `angle`
    degrees, kept where they lie on a straight line at the scale of a quad (the crease points within half a
    quad have one long axis: 1 - second / first eigenvalue over `straightness`) and form a connected line at
    least `min_length` quads long. Found on the sculpt, not the proxy: a voxel proxy rounds a corner over a
    voxel or two, which at low counts is as wide as the quads, so no fold test there tells a plinth's edge
    from a curl (on the Apollo Lykeios at 1k, a curl-free bust shows as many). Returns the crease points (edge
    midpoints) and their directions, or None when there are none."""
    from mathutils import Vector
    from mathutils.kdtree import KDTree
    a, b, c = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    normal = np.cross(b - a, c - a)
    normal /= np.maximum(np.linalg.norm(normal, axis=1), 1e-30)[:, None]
    pairs = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    pairs.sort(axis=1)
    key = pairs[:, 0].astype(np.int64) * len(co) + pairs[:, 1]
    face = np.tile(np.arange(len(tris)), 3)
    order = np.argsort(key, kind="stable")
    key, face = key[order], face[order]
    shared = np.flatnonzero(key[1:] == key[:-1])  # an edge between two faces
    folded = shared[(normal[face[shared]] * normal[face[shared + 1]]).sum(1) < np.cos(np.radians(angle))]
    if not len(folded):
        return None
    v1, v2 = key[folded] // len(co), key[folded] % len(co)
    mid = 0.5 * (co[v1] + co[v2])
    tree = KDTree(len(mid))
    for i, p in enumerate(mid):
        tree.insert(p, i)
    tree.balance()
    keep = np.zeros(len(mid), dtype=bool)
    direction = np.zeros_like(mid)
    # half a quad, or a few of the crease's own edges on a mesh coarser than that
    radius = max(0.5 * quad, 2.5 * float(np.median(np.linalg.norm(co[v1] - co[v2], axis=1))))
    for i, p in enumerate(mid):
        near = tree.find_range(Vector(p), radius)
        if len(near) < 4:
            continue
        pts = np.array([q for q, _, _ in near])
        pts -= pts.mean(axis=0)
        values, vectors = np.linalg.eigh(pts.T @ pts)
        if 1.0 - values[1] / max(values[2], 1e-30) > straightness:
            keep[i] = True
            direction[i] = vectors[:, 2]
    if not keep.any():
        return None
    root = {}  # connected lines: union-find over the kept edges' vertices

    def find(v):
        while root.get(v, v) != v:
            root[v] = root.get(root[v], root[v])
            v = root[v]
        return v
    for x, y in zip(v1[keep].tolist(), v2[keep].tolist()):
        rx, ry = find(x), find(y)
        if rx != ry:
            root[rx] = ry
    line = np.array([find(x) for x in v1[keep].tolist()])
    lengths = np.linalg.norm(co[v1[keep]] - co[v2[keep]], axis=1)
    labels, inverse = np.unique(line, return_inverse=True)
    long_enough = np.bincount(inverse, weights=lengths) >= min_length * quad
    chosen = long_enough[inverse]
    if not chosen.any():
        return None
    return mid[keep][chosen], direction[keep][chosen]


def ridge_lines(co, normals, edges, tx, ty, theta, k1, k2, quad, turn=RIDGE_TURN, length=RIDGE_LENGTH):
    """The ridges and valleys of the surface at the quad scale (nose, brow, lips, a hairline): vertices where
    the larger curvature turns the normal more than `turn` radians across one quad, is at least twice the
    smaller one, and is largest among the neighbours across the line (Ohtake, Belyaev and Seidel 2004's
    definition, curvature extremal across its own direction), joined along the line into chains at least
    `length` quads long. Returns a mask of those vertices and the direction along the line at each."""
    big = np.where(np.abs(k1) >= np.abs(k2), k1, k2)
    small = np.where(np.abs(k1) >= np.abs(k2), k2, k1)
    across_angle = np.where(np.abs(k1) >= np.abs(k2), theta, theta + np.pi / 2)
    across = np.cos(across_angle)[:, None] * tx + np.sin(across_angle)[:, None] * ty
    strength = np.abs(big) * quad
    candidate = (strength > turn) & (np.abs(big) > 2.0 * np.abs(small))
    peak = candidate.copy()
    ends = np.concatenate([edges, edges[:, ::-1]])
    a, b = ends[:, 0], ends[:, 1]
    step = co[b] - co[a]
    unit = step / np.maximum(np.linalg.norm(step, axis=1), 1e-20)[:, None]
    lies_across = np.abs((unit * across[a]).sum(1)) > 0.7
    beaten = lies_across & (strength[b] > strength[a]) & (np.sign(big[b]) == np.sign(big[a]))
    peak[a[beaten]] = False
    along_line = np.abs((unit * across[a]).sum(1)) < 0.5
    link = peak[a] & peak[b] & along_line & (a < b)
    root = np.arange(len(co))

    def find(v):
        while root[v] != v:
            root[v] = root[root[v]]
            v = root[v]
        return v
    for x, y in zip(a[link].tolist(), b[link].tolist()):
        rx, ry = find(x), find(y)
        if rx != ry:
            root[rx] = ry
    owner = np.array([find(v) for v in range(len(co))])
    run = np.bincount(owner[a[link]], weights=np.linalg.norm(step[link], axis=1), minlength=len(co))
    kept = peak & (run[owner] >= length * quad)
    return kept, np.cross(normals, across)


def frame_matching(x, y, a, b):
    """Which of b's four field directions (x, y, -x, -y) matches a's x, per pair of vertices a, b: 0 to 3."""
    candidates = np.stack([x[b], y[b], -x[b], -y[b]], axis=1)
    return np.argmax((candidates * x[a][:, None, :]).sum(axis=2), axis=1)


def target_sizes(x, y, theta_x, theta, k1, k2, thick, quad, edges, aspect=3.0, around=6, span=4.0):
    """Two quad sizes per vertex, relative to the uniform size: along the field direction x and across it.
    A quad side of length h bent by the normal curvature k misses its chord by about k h^2 / 8, so equal
    misses ask for h ~ |k|^-1/2 along each direction (k from the principal curvatures by Euler's formula; at
    a fixed area this puts the short side across the larger curvature, at the square root of the curvature
    ratio). Then: across the larger curvature, at most what gives `around` quads around a part as thick as
    `thick`; the long side at most `aspect` times the short one; every size within [1/span, span]. Returned
    as logs (n x 2), not yet scaled to the face count."""
    c, s = np.cos(theta_x - theta) ** 2, np.sin(theta_x - theta) ** 2
    bend = np.abs(np.stack([k1 * c + k2 * s, k1 * s + k2 * c], axis=1))  # normal curvature along x, along y
    count = np.maximum(np.bincount(edges.ravel(), minlength=len(x)), 1)[:, None]
    parity = frame_matching(x, y, edges[:, 0], edges[:, 1]) % 2

    def blur(v, rounds=4):  # a vertex's own noise is not form; neighbours' x and y swap where the frames do
        for _ in range(rounds):
            j_for_i = np.where(parity[:, None] == 1, v[edges[:, 1]][:, ::-1], v[edges[:, 1]])
            i_for_j = np.where(parity[:, None] == 1, v[edges[:, 0]][:, ::-1], v[edges[:, 0]])
            total = np.zeros_like(v)
            for axis in range(2):
                total[:, axis] = (np.bincount(edges[:, 0], weights=j_for_i[:, axis], minlength=len(v))
                                  + np.bincount(edges[:, 1], weights=i_for_j[:, axis], minlength=len(v)))
            v = 0.5 * (v + total / count)
        return v
    log_h = -0.5 * blur(np.log(np.maximum(bend * quad, 1e-6)))
    log_h -= np.median(log_h)
    bigger = np.argmax(bend, axis=1)  # the direction with the larger curvature gets the short side
    rows = np.arange(len(x))
    cap = np.log(np.maximum(np.pi * thick / (around * quad), 1e-6))
    log_h[rows, bigger] = np.minimum(log_h[rows, bigger], cap)
    log_h[rows, 1 - bigger] = np.minimum(log_h[rows, 1 - bigger], log_h[rows, bigger] + np.log(aspect))
    return np.clip(log_h, -np.log(span), np.log(span))


def consistent_sizes(log_h, co, tris, x, y, mass, quad, weight=0.05, rounds=6):
    """The quad sizes nearest log_h (n x 2, logs along x and y) that a quad grid on this field can have.
    Following a field without poles, a grid with sides s_x, s_y is an orthogonal net, and its coordinate
    1-forms x.dl / s_x and y.dl / s_y must be closed: around every triangle they sum to zero. That ties a
    size's change across its lines to how the lines bend (Liouville's formula: y(log s_x) = -k_x and
    x(log s_y) = k_y, k the geodesic curvature of each family against its left normal), so log s_x is fixed
    up to a function constant along each y-line and may be chosen freely along one curve crossing them; it
    is what QuadriFlow's integer layout enforces, keeping only the consistent part of any other size field.
    The sum is a first-order discretization: at sizes that fit, it shrinks as the triangle's area times its
    size (checked on a log-polar grid), at sizes that do not, as the area. Least squares on those circulations (triangles holding a pole of the field left out)
    plus `weight` times the mass-weighted distance to log_h, by Gauss-Newton with conjugate gradient. Returns
    the logs and the RMS circulation (in quads) before and after."""
    n, t = len(co), len(tris)
    match = [np.zeros(t, dtype=int)] + [frame_matching(x, y, tris[:, 0], tris[:, k]) for k in (1, 2)]
    closes = (frame_matching(x, y, tris[:, 0], tris[:, 1]) + frame_matching(x, y, tris[:, 1], tris[:, 2])
              + frame_matching(x, y, tris[:, 2], tris[:, 0])) % 4 == 0  # no pole of the field inside
    rows_t = np.arange(t)
    X, Y, cx, cy = [], [], [], []
    for k in range(3):
        v = tris[:, k]
        candidates = np.stack([x[v], y[v], -x[v], -y[v]], axis=1)
        X.append(candidates[rows_t, match[k]])
        Y.append(candidates[rows_t, (match[k] + 1) % 4])
        cx.append(2 * v + match[k] % 2)  # which of the vertex's two sizes runs along corner 0's x
        cy.append(2 * v + 1 - match[k] % 2)
    sides = []  # per triangle side: the 1-form coefficients (in quads) and the size entries at both ends
    for k in range(3):
        k1 = (k + 1) % 3
        edge = (co[tris[:, k1]] - co[tris[:, k]]) / quad
        sides.append(((0.5 * (X[k] + X[k1]) * edge).sum(1) * closes, cx[k], cx[k1], 0))
        sides.append(((0.5 * (Y[k] + Y[k1]) * edge).sum(1) * closes, cy[k], cy[k1], 1))
    m = np.repeat(mass / mass.mean(), 2)
    goal = log_h.ravel().copy()
    w = goal.copy()

    def residual(w):
        r = np.zeros(2 * t)
        for c, p, q, half in sides:
            r[2 * rows_t + half] += c * np.exp(-0.5 * (w[p] + w[q]))
        return r
    before = float(np.sqrt((residual(w) ** 2).mean()))
    for _ in range(rounds):
        entries = []  # Jacobian: (row, column, value)
        for c, p, q, half in sides:
            d = -0.5 * c * np.exp(-0.5 * (w[p] + w[q]))
            entries += [(2 * rows_t + half, p, d), (2 * rows_t + half, q, d)]
        jr = np.concatenate([e[0] for e in entries])
        jc = np.concatenate([e[1] for e in entries])
        jv = np.concatenate([e[2] for e in entries])

        def normal(v):  # (J^T J + weight M) v
            jv_v = np.bincount(jr, weights=jv * v[jc], minlength=2 * t)
            return np.bincount(jc, weights=jv * jv_v[jr], minlength=2 * n) + weight * m * v
        r = residual(w)
        rhs = -(np.bincount(jc, weights=jv * r[jr], minlength=2 * n) + weight * m * (w - goal))
        step, _ = conjugate_gradient(normal, rhs, np.zeros(2 * n), iterations=300, tolerance=1e-6)
        w += step
    after = float(np.sqrt((residual(w) ** 2).mean()))
    return w.reshape(n, 2), before, after


def same_count(log_h, mass):
    """Log sizes shifted so a mesh of them has as many faces as the uniform size gives: sum of area over
    s_x s_y unchanged. A shift keeps them consistent (consistent_sizes)."""
    return log_h + 0.5 * np.log(float((mass * np.exp(-log_h.sum(1))).sum()) / float(mass.sum()))


# ------------------------------------------------------------------------------------------- field solves

def connection(co, edges, tx, ty):
    """Transport along each edge i -> j between the two tangent bases, as a unit complex number: raised to
    the 4th for a cross field, and plain (1-fold), which the index count and the trivial connection need."""
    v = co[edges[:, 1]] - co[edges[:, 0]]
    at_i = local_angle(v, tx[edges[:, 0]], ty[edges[:, 0]])
    at_j = local_angle(-v, tx[edges[:, 1]], ty[edges[:, 1]])
    # The edge leaves i at angle at_i and arrives at j pointing the opposite way, at_j + pi.
    r1 = np.exp(1j * (at_j - at_i + np.pi))
    return r1 ** N_SYM, r1


def laplacian_apply(z, edges, weights, r):
    """(L z)_i = sum_j w_ij (z_i - transport_ji z_j), the connection Laplacian, applied through bincount."""
    i, j = edges[:, 0], edges[:, 1]
    from_j = weights * (z[i] - np.conj(r) * z[j])  # j's value carried to i
    from_i = weights * (z[j] - r * z[i])
    out = np.empty(len(z), dtype=np.complex128)
    out.real = np.bincount(i, weights=from_j.real, minlength=len(z)) + np.bincount(j, weights=from_i.real, minlength=len(z))
    out.imag = np.bincount(i, weights=from_j.imag, minlength=len(z)) + np.bincount(j, weights=from_i.imag, minlength=len(z))
    return out


def conjugate_gradient(apply, b, x0, iterations=400, tolerance=1e-7):
    x = x0.copy()
    r = b - apply(x)
    p = r.copy()
    rr = np.vdot(r, r).real
    b_norm = max(np.sqrt(np.vdot(b, b).real), 1e-30)
    for k in range(iterations):
        ap = apply(p)
        alpha = rr / max(np.vdot(p, ap).real, 1e-300)
        x += alpha * p
        r -= alpha * ap
        rr_new = np.vdot(r, r).real
        if np.sqrt(rr_new) < tolerance * b_norm:
            return x, k + 1
        p = r + (rr_new / rr) * p
        rr = rr_new
    return x, iterations


def aligned_field(edges, weights, r, mass, d, weight, start, iterations=400):
    """Minimizes the Dirichlet energy plus sum m_i w_i |z_i - d_i|^2: w is the alignment weight times the
    curvature confidence on free vertices, and a hard weight on constrained ones."""
    diag = mass * weight
    return conjugate_gradient(lambda x: laplacian_apply(x, edges, weights, r) + diag * x + 1e-6 * mass * x,
                              diag * d, start, iterations=iterations)


def sharp_constraints(co, tris, edges, edge_of_side, tx, ty, angle_degrees):
    """Vertices on edges where the surface folds by more than the angle, each asked to align with that edge.
    Measured on the unsmoothed proxy, since smoothing is what removes creases. Returns directions and a mask."""
    n = len(co)
    pinned = np.zeros(n, dtype=bool)
    d = np.zeros(n, dtype=np.complex128)
    if angle_degrees <= 0.0:
        return d, pinned
    a, b, c = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    face_normals = np.cross(b - a, c - a)
    face_normals /= np.maximum(np.linalg.norm(face_normals, axis=1), 1e-20)[:, None]
    order = np.argsort(edge_of_side.ravel(), kind="stable")  # each manifold edge appears twice among the sides
    edge_ids = edge_of_side.ravel()[order]
    tri_ids = np.repeat(np.arange(len(tris)), 3)[order]
    first = np.flatnonzero(np.r_[True, edge_ids[1:] != edge_ids[:-1]])
    counts = np.diff(np.r_[first, len(edge_ids)])
    two = first[counts == 2]
    cos_fold = (face_normals[tri_ids[two]] * face_normals[tri_ids[two + 1]]).sum(1)
    sharp = edge_ids[two][cos_fold < np.cos(np.radians(angle_degrees))]
    for end in (0, 1):
        v = edges[sharp, end]
        d[v] = np.exp(4j * local_angle(co[edges[sharp, 1 - end]] - co[v], tx[v], ty[v]))
        pinned[v] = True
    return d, pinned


def boundary_constraints(co, tris, edges, edge_of_side, tx, ty):
    """Vertices on open edges, each asked to align with them, so quads run along necklines, holes and hems
    and end on them square-on. A vertex between two open edges takes their 4-fold average (at a corner of
    90 degrees both edges agree as crosses). Returns directions and a mask."""
    n = len(co)
    d = np.zeros(n, dtype=np.complex128)
    uses = np.bincount(edge_of_side.ravel(), minlength=len(edges))
    open_edges = np.flatnonzero(uses == 1)
    for end in (0, 1):
        v = edges[open_edges, end]
        angle = local_angle(co[edges[open_edges, 1 - end]] - co[v], tx[v], ty[v])
        np.add.at(d, v, np.exp(4j * angle))
    on = np.abs(d) > 1e-6
    d[on] /= np.abs(d[on])
    return d, on


def symmetry_constraints(co, normals, tx, ty, quad, axis):
    """Vertices within one quad of the mirror plane, asked to align with the plane's normal, so quads meet the
    seam square-on and the mirrored half welds to a clean loop. Returns directions and a mask."""
    n = len(co)
    near = np.abs(co[:, axis]) < quad
    d = np.zeros(n, dtype=np.complex128)
    plane_normal = np.zeros((n, 3))
    plane_normal[:, axis] = 1.0
    plane_normal -= (plane_normal * normals).sum(1)[:, None] * normals
    usable = near & (np.linalg.norm(plane_normal, axis=1) > 1e-6)
    d[usable] = np.exp(4j * local_angle(plane_normal[usable], tx[usable], ty[usable]))
    return d, usable


def face_indices(z, tris, edge_of_side, edges, r, r1):
    """Each face's singularity index: the field's turning around it, in quarter turns, plus N_SYM times the
    holonomy of the plain transport around it (taken 1-fold, then multiplied, so a face that turns by more
    than a quarter turn, a sliver, still counts right)."""
    unit = z / np.maximum(np.abs(z), 1e-20)
    total = np.zeros(len(tris))
    holonomy = np.ones(len(tris), dtype=np.complex128)
    for side in range(3):
        a, b = tris[:, side], tris[:, (side + 1) % 3]
        e = edge_of_side[:, side]
        forward = edges[e, 0] == a
        total += np.angle(unit[b] * np.conj(np.where(forward, r[e], np.conj(r[e])) * unit[a]))
        holonomy *= np.where(forward, r1[e], np.conj(r1[e]))
    total += N_SYM * np.angle(holonomy)
    return np.rint(total / (2 * np.pi)).astype(int)


def face_holonomy(tris, edges, edge_of_side, angles):
    """Per face, the sum of the signed per-edge angles around it, and the signs used (+1 where a side runs
    along its edge's own direction)."""
    sign = np.where(edges[edge_of_side, 0] == tris, 1.0, -1.0)
    return (sign * angles[edge_of_side]).sum(axis=1), sign


# -------------------------------------------------------------------------------------- trivial connection

def spanning_forest(n_vertices, edges):
    """A breadth-first spanning tree of each connected piece: parent vertex and edge per vertex (a root is its
    own parent), the visiting order, and the piece each vertex belongs to."""
    order_by_vertex = np.argsort(np.concatenate([edges[:, 0], edges[:, 1]]), kind="stable")
    other = np.concatenate([edges[:, 1], edges[:, 0]])[order_by_vertex]
    edge_id = np.concatenate([np.arange(len(edges))] * 2)[order_by_vertex]
    starts = np.searchsorted(np.concatenate([edges[:, 0], edges[:, 1]])[order_by_vertex], np.arange(n_vertices + 1))
    other, edge_id, starts = other.tolist(), edge_id.tolist(), starts.tolist()
    parent = [-1] * n_vertices
    parent_edge = [-1] * n_vertices
    piece = [-1] * n_vertices
    order = []
    for root in range(n_vertices):
        if parent[root] >= 0:
            continue
        parent[root] = root
        piece[root] = root
        queue = [root]
        for v in queue:  # the list grows as it is walked
            for k in range(starts[v], starts[v + 1]):
                w = other[k]
                if parent[w] < 0:
                    parent[w], parent_edge[w], piece[w] = v, edge_id[k], root
                    queue.append(w)
        order.extend(queue)
    _, piece = np.unique(piece, return_inverse=True)
    return np.array(parent), np.array(parent_edge), np.array(order), piece


def loose_edges(tris, edges, edge_of_side, parent_edge, loop_of_edge=None):
    """Tree-cotree: the edges in neither the vertex forest nor a spanning forest of the faces across the
    remaining edges. Each closes one homology generator, two per handle. Every open loop is capped by its own
    node of the dual graph (loop_of_edge: the loop of each open edge, -1 elsewhere), so the surface is closed
    up and exactly 2g edges are left, all of them handles: none runs around an open loop, whose holonomy the
    faces and the loop conditions decide already."""
    in_tree = np.zeros(len(edges), dtype=bool)
    in_tree[parent_edge[parent_edge >= 0]] = True
    outside = len(tris)
    side_faces = np.repeat(np.arange(len(tris)), 3)
    side_edges = edge_of_side.ravel()
    order = np.argsort(side_edges, kind="stable")
    sorted_edges, sorted_faces = side_edges[order], side_faces[order]
    first = np.searchsorted(sorted_edges, np.arange(len(edges)))
    count = np.bincount(side_edges, minlength=len(edges))
    face_a = sorted_faces[first]
    cap = outside if loop_of_edge is None else outside + np.maximum(loop_of_edge, 0)
    face_b = np.where(count >= 2, sorted_faces[np.minimum(first + 1, len(sorted_faces) - 1)], cap)
    free = np.flatnonzero(~in_tree)
    # union-find over faces (and the cap nodes) across the free edges: an edge that joins two different sets
    # is a dual tree edge, one that closes a loop is a generator
    root = list(range(len(tris) + 1 + (int(loop_of_edge.max()) + 1 if loop_of_edge is not None else 0)))

    def find(f):
        while root[f] != f:
            root[f] = root[root[f]]
            f = root[f]
        return f
    loose = []
    for e, a, b in zip(free.tolist(), face_a[free].tolist(), face_b[free].tolist()):
        ra, rb = find(a), find(b)
        if ra == rb:
            loose.append(e)
        else:
            root[ra] = rb
    return np.array(loose, dtype=np.int64)


def _tree_path(parent, a, b):
    """The vertices from a and from b up to where their paths through the tree meet (that vertex excluded)."""
    up_a, seen = [], {}
    v = a
    while True:
        seen[v] = len(up_a)
        if parent[v] == v:
            break
        up_a.append(v)
        v = parent[v]
    up_b = []
    w = b
    while w not in seen:
        up_b.append(w)
        w = parent[w]
    return up_a[:seen[w]], up_b


def choose_indices(index, tris, co, quad, reach, totals, face_piece):
    """The singularities the trivial connection will be given: the field's own, with +1/-1 pairs closer than
    `reach` quads dropped (the swirls), and each piece's sum repaired to what its face holonomies demand by
    dropping the surplus singularities that sit nearest another. Returns the indices and how many went."""
    from mathutils import Vector
    from mathutils.kdtree import KDTree
    k = index.copy()
    centres = co[tris].mean(axis=1)

    def tree_of(faces):
        tree = KDTree(len(faces))
        for n, f in enumerate(faces):
            tree.insert(Vector(centres[f]), n)
        tree.balance()
        return tree
    plus, minus = np.flatnonzero(k > 0), np.flatnonzero(k < 0)
    dropped = 0
    if len(plus) and len(minus):
        # every +1/-1 pair within reach, nearest first, each singularity used once
        near_minus = tree_of(minus)
        pairs = sorted((gap, i, j) for i, f in enumerate(plus)
                       for _co, j, gap in near_minus.find_range(Vector(centres[f]), reach * quad)
                       if face_piece[f] == face_piece[minus[j]])
        used_plus, used_minus = set(), set()
        for _gap, i, j in pairs:
            if i in used_plus or j in used_minus:
                continue
            used_plus.add(i)
            used_minus.add(j)
            k[plus[i]] -= 1
            k[minus[j]] += 1
            dropped += 2
    for piece, total in enumerate(totals):
        mine = face_piece == piece
        surplus = int(k[mine].sum() - total)
        while surplus != 0:
            sign = 1 if surplus > 0 else -1
            candidates = np.flatnonzero(mine & (k * sign > 0))
            if not len(candidates):  # nothing to take away: add one of the needed sign at the piece's first face
                k[np.flatnonzero(mine)[0]] -= sign
            else:
                others = np.flatnonzero(mine & (k != 0))
                if len(others) > 1:  # the candidate nearest another singularity
                    tree = tree_of(others)
                    pick = min(candidates, key=lambda f: tree.find_n(Vector(centres[f]), 2)[1][2])
                else:
                    pick = candidates[0]
                k[pick] -= sign
            surplus -= sign
            dropped += 1
    return k, dropped


def trivial_connection(target, tris, edges, edge_of_side, r1, parent, parent_edge, loops=(), loop_of_edge=None,
                       iterations=2000):
    """The smallest per-edge change x of the connection angle that puts exactly the target indices on the
    faces (Crane, Desbrun and Schroder 2010), with the holonomy around each homology generator rounded to a
    quarter turn so the field closes up around handles. Returns x (radians per edge, in its own direction)
    and a report.

    loops: one (edge ids, signs, change needed) per loop of open edges: how far the connection must turn in
    all around that loop for a field lying along the edge at its start to lie along it again at its end
    (Crane et al.'s boundary condition, taken per loop rather than per edge so a jagged edge does not force
    its jags onto the few edges next to it). Give all but one loop of each piece: the faces decide the last.
    loop_of_edge: which open loop each open edge is on (-1 elsewhere), so tree-cotree caps each loop and finds
    only the handles (see loose_edges).

    The face rows alone are the dual graph's Laplacian, which conjugate gradient solves; it stops once every
    face is within a fiftieth of a radian of its target, far inside the quarter turn that would change an
    index. Loop rows, then generator rows, are added after as corrections inside the face rows' null space."""
    n_e = len(edges)
    base = np.angle(r1)
    face_hol, sign = face_holonomy(tris, edges, edge_of_side, base)
    # The change each face needs: from its curvature (the wrapped holonomy, the angle defect) to its target.
    # Wrapping the holonomy, not the difference, keeps each piece's rows consistent: they sum to zero.
    b_faces = 2 * np.pi * target / N_SYM - np.angle(np.exp(1j * face_hol))

    def A_f(x):
        return (sign * x[edge_of_side]).sum(axis=1)

    def At_f(y):
        x = np.zeros(n_e)
        for side in range(3):
            x += np.bincount(edge_of_side[:, side], weights=sign[:, side] * y, minlength=n_e)
        return x

    def least_norm_faces(rhs, tolerance=0.02):
        """x of least norm with A_f x = rhs: x = A_f^T y, (A_f A_f^T) y = rhs, until every row is within
        `tolerance`."""
        y = np.zeros(len(rhs))
        res = rhs.copy()
        p = res.copy()
        rr = res @ res
        k = 0
        for k in range(1, iterations + 1):
            ap = A_f(At_f(p))
            alpha = rr / max(p @ ap, 1e-300)
            y += alpha * p
            res -= alpha * ap
            if np.abs(res).max() < tolerance:
                break
            rr_new = res @ res
            p = res + (rr_new / rr) * p
            rr = rr_new
        return At_f(y), k

    def in_null_space(g):
        """g with its part in the face rows' span taken out: a change along it leaves every face as it is."""
        return g - least_norm_faces(A_f(g), tolerance=0.005)[0]

    x, steps = least_norm_faces(b_faces)

    # open loops: exact
    loop_rows = []
    for es, ss, needed in loops:
        g = np.zeros(n_e)
        np.add.at(g, es, ss)
        loop_rows.append((g, needed))
    taken = []  # projections used, to keep the generators off them
    if loop_rows:
        taken = [in_null_space(g) for g, _ in loop_rows]
        projected = np.array(taken)
        # a cross field only needs the loop's turn right to a quarter turn: take the nearest such target
        want = np.array([needed - g @ x for g, needed in loop_rows])
        want -= np.round(want / (np.pi / 2)) * (np.pi / 2)
        x = x + np.linalg.lstsq(projected @ projected.T, want, rcond=1e-9)[0] @ projected

    # handles: rounded to the nearest quarter turn from where the solve has left them
    generators = loose_edges(tris, edges, edge_of_side, parent_edge, loop_of_edge)
    rows, projected, want = [], [], []
    for e in generators:
        a, b = edges[e]
        up_a, up_b = _tree_path(parent, a, b)
        es, ss = [], []
        for v in up_a:  # from a up to the meeting point
            pe = parent_edge[v]
            es.append(pe)
            ss.append(1.0 if edges[pe, 0] == v else -1.0)
        for v in reversed(up_b):  # down from the meeting point to b
            pe = parent_edge[v]
            es.append(pe)
            ss.append(1.0 if edges[pe, 0] == parent[v] else -1.0)
        es.append(e)  # and back from b to a along the loose edge
        ss.append(-1.0)
        g = np.zeros(n_e)
        np.add.at(g, np.array(es), np.array(ss))
        p = in_null_space(g)
        if taken:  # keep the handle's correction from undoing the loops'
            t = np.array(taken)
            p = p - np.linalg.lstsq(t @ t.T, t @ p, rcond=1e-9)[0] @ t
        holonomy = g @ (base + x)
        rows.append(g)
        projected.append(p)
        want.append(np.round(holonomy / (np.pi / 2)) * (np.pi / 2) - holonomy)
    if rows:
        projected = np.array(projected)
        x = x + np.linalg.lstsq(projected @ projected.T, np.array(want), rcond=1e-9)[0] @ projected
    face_error = np.abs(np.angle(np.exp(1j * (A_f(x) - b_faces))))
    loop_error = max((abs(np.angle(np.exp(4j * (g @ x - needed)))) / 4 for g, needed in loop_rows), default=0.0)
    return x, {"generators": len(generators), "generators_used": len(rows), "open_loops": len(loop_rows),
               "cg_steps": steps, "face_error_max_deg": float(np.degrees(face_error.max())),
               "loop_error_max_deg": float(np.degrees(loop_error))}


def open_loops(tris, edges, edge_of_side, r1, d):
    """The loops of open edges, each as (edge ids, signs, change needed): the signs are the ones the edge's
    single face walks it with, and the change is the sum over the loop of what each edge alone would need to
    carry the field (d, along the edge) at one end onto the field at the other, to the nearest quarter turn."""
    uses = np.bincount(edge_of_side.ravel(), minlength=len(edges))
    open_edges = np.flatnonzero(uses == 1)
    if not len(open_edges):
        return []
    sign_of_side = np.where(edges[edge_of_side, 0] == tris, 1.0, -1.0)
    edge_sign = np.zeros(len(edges))
    edge_sign[edge_of_side.ravel()] = sign_of_side.ravel()  # an open edge has one side, so one sign
    theta = np.angle(d) / N_SYM
    i, j = edges[open_edges, 0], edges[open_edges, 1]
    turn = theta[j] - theta[i] - np.angle(r1[open_edges])
    each = turn - np.round(turn / (np.pi / 2)) * (np.pi / 2)  # in the edge's own direction
    # loops: the open edges' connected pieces (union-find on their vertices)
    root = {}

    def find(v):
        root.setdefault(v, v)
        while root[v] != v:
            root[v] = root[root[v]]
            v = root[v]
        return v
    for a, b in zip(i.tolist(), j.tolist()):
        ra, rb = find(a), find(b)
        if ra != rb:
            root[ra] = rb
    label = np.array([find(a) for a in i.tolist()])
    loops = []
    for key in np.unique(label):
        mine = label == key
        es = open_edges[mine]
        ss = edge_sign[es]
        loops.append((es, ss, float((ss * each[mine]).sum())))
    return loops


def walk_field(x, parent, parent_edge, order, edges, r1):
    """The parallel field of the adjusted connection: angle 0 at each root, carried out along the tree."""
    theta = np.zeros(len(parent))
    step = np.angle(r1) + x
    for v in order:
        u = parent[v]
        if u == v:
            continue
        e = parent_edge[v]
        theta[v] = theta[u] + (step[e] if edges[e, 0] == u else -step[e])
    return np.exp(4j * theta)


def field_vectors(z, tx, ty):
    """A cross field value back to one of its four 3D tangent directions."""
    theta = np.angle(z) / N_SYM
    return np.cos(theta)[:, None] * tx + np.sin(theta)[:, None] * ty


# ------------------------------------------------------------------------------------------------- solve

def solve(co, tris, target, smoothing=3.0, align=1.0, reach=6.0, realign=1.0, mirror_axis=None,
          sharp_angle=0.0, hard=50.0, adaptive=0.0, creases=None, ridges=False, log=None):
    """The form-following cross field on a proxy. co, tris: the proxy (float64 vertices, triangles); target:
    the face count the remesh aims for (on the proxy as given, so half of it for a halved proxy).

    Returns a dict: positions (the smoothed proxy, which the engine must be given so its tangent planes are
    the field's), directions (one tangent vector per vertex), scale (n x 2 or None: per vertex the quad's
    length along its direction and across it, relative to the uniform quad size; adaptive, 0 to 1, scales
    the logs of target_sizes), and a report.
    log(text) is called with progress, if given."""
    import time
    say = log or (lambda text: None)
    report = {}
    started = time.perf_counter()
    edges, edge_of_side = unique_edges(tris)
    _, _, area = vertex_normals_and_areas(co, tris)
    open_edges = np.bincount(edge_of_side.ravel(), minlength=len(edges)) == 1
    on_crease = np.zeros(len(co), dtype=bool)
    crease_direction = None
    if creases is not None:
        # proxy vertices beside a crease of the sculpt are pinned along it in the field. Also leaving them
        # unsmoothed and giving QuadriFlow a line through the crease there over-constrains it: on the Apollo
        # Lykeios at 1k, 93 folds and the sculpt missed p95 4.6%, where the pin alone gives 2.8% (5.5% without)
        from mathutils import Vector
        from mathutils.kdtree import KDTree
        points, directions = creases
        tree = KDTree(len(points))
        for i, p in enumerate(points):
            tree.insert(p, i)
        tree.balance()
        reach = 0.75 * float(np.median(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)))
        crease_direction = np.zeros((len(co), 3))
        for i, p in enumerate(co):
            _q, k, d = tree.find(Vector(p))
            if d < reach:
                on_crease[i] = True
                crease_direction[i] = directions[k]
        report["crease_vertices"] = int(on_crease.sum())
    smooth, report["smoothing_steps"] = taubin(co, edges, target, area, smoothing, open_edges)
    normals, mass, area = vertex_normals_and_areas(smooth, tris)
    quad = (area / target) ** 0.5
    tx, ty = tangent_bases(smooth, normals, edges)
    theta, k1, k2 = principal_directions(smooth, normals, edges, tx, ty)
    confidence = confidence_of(k1, k2, quad)
    weights = cotan_weights(smooth, tris, edge_of_side)
    r, r1 = connection(smooth, edges, tx, ty)
    report["setup_seconds"] = round(time.perf_counter() - started, 2)
    say("setup %.1fs" % report["setup_seconds"])

    # What the field is asked to follow: curvature where it means something, hard constraints on sharp edges
    # (measured before smoothing) and along the mirror plane.
    d = np.exp(4j * theta)
    pinned = np.zeros(len(co), dtype=bool)
    sharp_d, sharp_mask = sharp_constraints(co, tris, edges, edge_of_side, tx, ty, sharp_angle)
    d[sharp_mask], pinned[sharp_mask] = sharp_d[sharp_mask], True
    open_d, open_mask = boundary_constraints(smooth, tris, edges, edge_of_side, tx, ty)
    d[open_mask], pinned[open_mask] = open_d[open_mask], True
    if on_crease.any():
        d[on_crease] = np.exp(4j * local_angle(crease_direction[on_crease], tx[on_crease], ty[on_crease]))
        pinned[on_crease] = True
    report["open_edge_vertices"] = int(open_mask.sum())
    if mirror_axis is not None:
        mirror_d, mirror_mask = symmetry_constraints(smooth, normals, tx, ty, quad, mirror_axis)
        d[mirror_mask], pinned[mirror_mask] = mirror_d[mirror_mask], True
    weight = align * confidence
    weight[pinned] = hard

    started = time.perf_counter()
    z, report["cg_steps"] = aligned_field(edges, weights, r, mass, d, weight, d.copy())
    index = face_indices(z, tris, edge_of_side, edges, r, r1)
    report["aligned_singularities"] = int((index != 0).sum())
    report["aligned_seconds"] = round(time.perf_counter() - started, 2)
    say("aligned field: %d singularities, %.1fs" % (report["aligned_singularities"], report["aligned_seconds"]))

    if reach > 0.0:
        started = time.perf_counter()
        parent, parent_edge, order, piece = spanning_forest(len(co), edges)
        face_piece = piece[tris[:, 0]]
        # Each piece's indices must sum to what its face holonomies sum to: 4 times its Euler characteristic
        # in exact arithmetic, and whatever the wrapped angles give on a proxy with a face that turns by more
        # than a quarter turn; the solve has no solution otherwise.
        face_hol, _ = face_holonomy(tris, edges, edge_of_side, np.angle(r1))
        loops = open_loops(tris, edges, edge_of_side, r1, d)
        # Per piece: its Euler characteristic and open loops give its genus, (2 - chi - b) / 2, reported; one
        # open loop per piece is decided by the faces and the others, so it is not given.
        n_pieces = piece.max() + 1
        chi = (np.bincount(piece, minlength=n_pieces) - np.bincount(piece[edges[:, 0]], minlength=n_pieces)
               + np.bincount(face_piece, minlength=n_pieces))
        loop_piece = np.array([piece[edges[es[0], 0]] for es, _, _ in loops], dtype=int)
        b = np.bincount(loop_piece, minlength=n_pieces)
        report["genus"] = [int(g) for g in (2 - chi - b) // 2]
        loop_of_edge = np.full(len(edges), -1)
        for number, (es, _, _) in enumerate(loops):
            loop_of_edge[es] = number
        # Each piece's indices must sum to what its face holonomies sum to, plus, where it has open edges,
        # the turn its open loops are given; the solve has no solution otherwise.
        wrapped = np.angle(np.exp(1j * face_hol))
        raw = N_SYM * np.bincount(face_piece, weights=wrapped, minlength=piece.max() + 1) / (2 * np.pi)
        for es, ss, needed in loops:
            raw[piece[edges[es[0], 0]]] += N_SYM * needed / (2 * np.pi)
        totals = np.rint(raw).astype(int)
        report["index_total_off"] = float(np.abs(raw - totals).max())  # should be ~0: the rows are consistent
        chosen, report["dropped"] = choose_indices(index, tris, smooth, quad, reach, totals, face_piece)
        given, seen = [], set()
        for (es, ss, needed), lp in zip(loops, loop_piece.tolist()):
            if lp in seen:
                given.append((es, ss, needed))
            seen.add(lp)
        x, tc = trivial_connection(chosen, tris, edges, edge_of_side, r1, parent, parent_edge, loops=given,
                                   loop_of_edge=loop_of_edge)
        report.update(tc)
        r1 = r1 * np.exp(1j * x)
        r = r1 ** N_SYM
        z = walk_field(np.zeros(len(edges)), parent, parent_edge, order, edges, r1)
        if realign > 0.0:  # follow curvature again, on the adjusted connection: the poles stay where they are
            weight = realign * confidence
            weight[pinned] = hard
            z, _ = aligned_field(edges, weights, r, mass, d, weight, z)
        index = face_indices(z, tris, edge_of_side, edges, r, r1)
        report["pieces"] = len(totals)
        on_open = np.zeros(len(edges), dtype=bool)
        for es, _, _ in loops:
            on_open[es] = True
        open_x, inner_x = np.abs(x[on_open]), np.abs(x[~on_open])
        report["adjustment_deg"] = {"inner_rms": float(np.degrees(np.sqrt((inner_x ** 2).mean()))),
                                    "inner_max": float(np.degrees(inner_x.max())),
                                    "open_max": float(np.degrees(open_x.max())) if len(open_x) else 0.0}
        report["trivial_seconds"] = round(time.perf_counter() - started, 2)
        say("poles placed: %d singularities, %.1fs" % (int((index != 0).sum()), report["trivial_seconds"]))
    report["singularities"] = int((index != 0).sum())
    # What the engine sees: the same directions measured against the surface's own connection. Equal to the
    # count above while the trivial connection's change stays well under an eighth of a turn per edge.
    r_plain, r1_plain = connection(smooth, edges, tx, ty)
    report["plain_singularities"] = int((face_indices(z, tris, edge_of_side, edges, r_plain, r1_plain) != 0).sum())

    scale = None
    if adaptive > 0.0:
        started = time.perf_counter()
        x = field_vectors(z, tx, ty)
        y = np.cross(normals, x)
        thick = thickness(smooth, normals, tris, tx, ty)
        goal = adaptive * target_sizes(x, y, np.angle(z) / N_SYM, theta, k1, k2, thick, quad, edges)
        log_h, before, after = consistent_sizes(goal, smooth, tris, x, y, mass, quad, weight=SIZE_FIT)
        log_h = same_count(log_h, mass)
        mean = log_h.mean(1)
        report["size_spread"] = {"asked": round(float(np.exp(np.percentile(goal.mean(1), 90) - np.percentile(goal.mean(1), 10))), 2),
                                 "consistent": round(float(np.exp(np.percentile(mean, 90) - np.percentile(mean, 10))), 2)}
        report["size_stretch"] = round(float(np.exp(np.median(np.abs(log_h[:, 0] - log_h[:, 1])))), 2)
        report["size_circulation"] = [round(before, 4), round(after, 4)]
        report["size_seconds"] = round(time.perf_counter() - started, 2)
        say("sizes: spread %s, stretch %.2f, %.1fs" % (report["size_spread"], report["size_stretch"], report["size_seconds"]))
        scale = np.exp(log_h)
    found = None
    if ridges:
        # ridge and valley lines, for the caller to move the quads' loops onto (remesh.py, _snap_to_ridges):
        # the field already points along them, it is the loops' place that is free. Pulling QuadriFlow's
        # lattice onto them with its own line constraints did not hold through its integer layout and the
        # snapping after it (on the Head of David at 8.9k: 30% of ridge points with an edge along them, 23 to
        # 32% with the pull, ZRemesher 48%)
        on_ridge, along = ridge_lines(smooth, normals, edges, tx, ty, theta, k1, k2, quad)
        found = (smooth[on_ridge], along[on_ridge])
        report["ridge_vertices"] = int(on_ridge.sum())
    return {"positions": smooth, "directions": field_vectors(z, tx, ty), "scale": scale, "ridges": found,
            "quad": quad, "report": report}


def has_quadriflow():
    from . import remesh_instant
    return getattr(remesh_instant.sculpt_kit_remesh, "quadriflow", None) is not None


def _quadriflow(mesh, target, directions, boundary, seed=0, scale=None):
    """Rebuild the mesh as QuadriFlow's pure quads on our frozen field. Returns its numbers. With open edges
    (boundary) its own hole filling is off, since it would close small real openings and a half's cut too;
    remesh() fills the holes that are neither."""
    from . import remesh_instant
    co = coordinates(mesh).astype(np.float32)
    steering = {"fill_holes": False} if boundary else {}
    tris = triangles(mesh)
    if scale is not None:  # only then: engines built before the size field do not know the argument
        steering["scale"] = np.ascontiguousarray(scale, dtype=np.float32)  # n x 2, along and across `directions`
    run = lambda asked: remesh_instant.sculpt_kit_remesh.quadriflow(
        co, tris, target_faces=asked, orientation=np.ascontiguousarray(directions, dtype=np.float32),
        preserve_boundary=boundary, seed=seed, **steering)
    out = run(target)
    if scale is not None and abs(len(out["quads"]) - target) > 0.1 * target and len(out["quads"]):
        # With uneven sizes QuadriFlow's count lands well off its target (5,547 for 8,933 on the Head of
        # David): once more, asking for the target scaled by the miss.
        out = run(int(target * target / len(out["quads"])))
    if not len(out["quads"]):
        raise RuntimeError("QuadriFlow gave no faces")
    remesh_instant._replace_geometry(mesh, out["vertices"], {4: out["quads"]})
    return {"engine": "quadriflow", "engine_singularities": out["singularities"],
            "position_singularities": out["position_singularities"], "vertices_added": out["vertices_added"],
            "remesh": out["seconds"]}


def remesh(ob, target, context, smoothing=1.0, align=1.0, mirror_axis=None, adaptive=0.0, seed=0, back_end=None,
           detect_edges=False, ridges=False, log=None):
    """The form-following remesh of the object's mesh as a new mesh (not yet projected onto the sculpt), a
    report, a BVH of the smoothed proxy (the surface at quad scale, for patches the quads cannot follow), and
    with ridges the ridge and valley lines found (points and directions along them, ridge_lines), else None.
    detect_edges: the field is pinned along the sculpt's hard edges (sculpt_creases).
    Creases are not kept yet: found on the proxy, by fold angle alone, they pick up a scan's curls and noise
    and add poles (Head of David at 5k: 22 irregular vertices without, 42 with folds over 90 degrees). seed: another layout of the same
    field (QuadriFlow starts its position field at random). back_end: "quadriflow" or "instant"; None picks
    QuadriFlow when the wheel has it. adaptive: 0 for one quad size, up to 1 for quads short across curvature
    and long along it, and smaller on thin parts (target_sizes, made consistent with the field). Holes the engine leaves are filled,
    except the sculpt's own openings (which the caller keeps, remesh._fill_stray_holes) and a half's cut.
    Raises RuntimeError when the engine is missing or fails."""
    from . import remesh_instant
    if not remesh_instant.available() or remesh_instant.sculpt_kit_remesh is None:
        raise RuntimeError("the native remesh engine is not built for this platform")
    if back_end is None:
        back_end = "quadriflow" if has_quadriflow() else "instant"
    mesh = make_proxy(ob, proxy_faces(target), context)
    open_sculpt = bool((np.bincount(_corner_edges(mesh), minlength=len(mesh.edges)) == 1).any())
    asymmetric = False
    try:
        if mirror_axis is not None:
            co, tris = coordinates(mesh), triangles(mesh)
            _, _, area = vertex_normals_and_areas(co, tris)
            if is_symmetric(co, tris, mirror_axis, 0.5 * (area / target) ** 0.5):
                halve(mesh, mirror_axis)
            else:
                mirror_axis, asymmetric = None, True
        share = target // 2 if mirror_axis is not None else target
        co, tris = coordinates(mesh), triangles(mesh)
        if not len(tris):
            raise RuntimeError("nothing left to remesh")
        creases = None
        if detect_edges:
            sculpt_co, sculpt_tris = coordinates(ob.data), triangles(ob.data)
            _, _, sculpt_area = vertex_normals_and_areas(sculpt_co, sculpt_tris)
            creases = sculpt_creases(sculpt_co, sculpt_tris, (sculpt_area / target) ** 0.5)
        field = solve(co, tris, share, smoothing=3.0 * smoothing, align=align, mirror_axis=mirror_axis,
                      adaptive=adaptive, creases=creases, ridges=ridges, log=log)
        mesh.attributes["position"].data.foreach_set("vector", field["positions"].astype(np.float32).ravel())
        mesh.update()
        has_open = open_sculpt or mirror_axis is not None
        if back_end == "quadriflow":
            # open edges (a half, or an open sculpt) must be kept, or QuadriFlow's lattice frays along them
            stats = _quadriflow(mesh, share, field["directions"], boundary=has_open, seed=seed, scale=field["scale"])
        else:
            stats = remesh_instant.remesh(mesh, share, pure_quad=True, orientation=field["directions"],
                                          orientation_weight=np.ones(len(co), dtype=np.float32),
                                          freeze_orientation=True, align_to_boundaries=mirror_axis is not None,
                                          fill_holes=not has_open,
                                          edge_length=None if field["scale"] is None
                                          else np.sqrt(field["scale"].prod(axis=1)) * field["quad"])
        if has_open and not open_sculpt:
            # a half of a closed sculpt: every hole but the cut is the engine's own
            cut = 0.3 * field["quad"]
            remesh_instant._fill_holes(mesh, keep=lambda verts: max(abs(v.co[mirror_axis]) for v in verts) < cut)
        if mirror_axis is not None:
            stats["merged_at_seam"] = mirror_and_weld(mesh, mirror_axis, 0.3 * field["quad"])
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    global last_report
    last_report = {**field["report"], **{k: v for k, v in stats.items() if k != "sides"}, "asymmetric": asymmetric}
    from mathutils.bvhtree import BVHTree
    coarse = BVHTree.FromPolygons(field["positions"].tolist(), tris.tolist())  # the surface at quad scale
    if mirror_axis is not None:  # only the half was solved: mirror it, so the whole remesh has a coarse surface
        other = field["positions"].copy()
        other[:, mirror_axis] *= -1.0
        coarse = BVHTree.FromPolygons(np.concatenate([field["positions"], other]).tolist(),
                                      np.concatenate([tris, tris[:, ::-1] + len(other)]).tolist())
    ridges_found = field["ridges"]
    if ridges_found is not None and mirror_axis is not None:  # only the half was solved: its mirror image too
        points, along = ridges_found
        flip = np.ones(3)
        flip[mirror_axis] = -1.0
        ridges_found = (np.concatenate([points, points * flip]), np.concatenate([along, along * flip]))
    return mesh, last_report, coarse, ridges_found


last_report = {}  # the last remesh's numbers, for the benchmark
