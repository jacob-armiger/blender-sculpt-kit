"""Option C, step 1: a curvature-aligned cross field on a proxy of the sculpt, in numpy, inside Blender.

    blender -b figure.blend --python tools/field_prototype.py -- [--object NAME] [--target 20000]
                                                                [--proxy 100000] [--align 1.0,10.0] [--out DIR]

The question it answers: does a field computed at the target quad size, aligned to curvature, have far
fewer singularities on a sculpt than the one Instant Meshes / QuadriFlow compute from the raw mesh? If so,
Option C's engine is worth building (the field goes into native/'s `orientation` hook).

What it does: decimates a copy to the proxy count, smooths it to the target quad size (Option A's Taubin
schedule), estimates per-vertex principal directions from the one-ring normals, builds the complex
connection Laplacian of Knoppel et al. 2013 (cotan weights, 4-fold transport), and solves
(L + align * M * C) z = align * M * C * d by conjugate gradient, where d is the principal direction to the
fourth power and C its confidence (anisotropy). Then it counts singularities by face index (their sum must
be 8 on a closed genus-0 surface: 4 times the Euler characteristic) and saves:

    <out>/field.npz    proxy vertices, faces, per-vertex direction (3D) and confidence, for native/'s remesh()
    <out>/field.blend  the proxy plus a "field" object of short line segments along the field, for looking at

Pure numpy and bincount, no scipy: the matrix is never formed, only applied.
"""
import argparse
import json
import os
import sys
import time

import bpy
import numpy as np


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--object", default="")
    p.add_argument("--target", type=int, default=20000)
    p.add_argument("--proxy", type=int, default=100000)
    p.add_argument("--proxy-method", default="decimate", choices=("decimate", "voxel"),
                   help="how the proxy is made: Decimate (collapse) or a Voxel Remesh at the matching size")
    p.add_argument("--align", default="0,1,10")
    p.add_argument("--smoothing", type=float, default=1.0)
    p.add_argument("--out", default="tests/out/field")
    p.add_argument("--sphere-faces", type=int, default=1_300_000)
    p.add_argument("--sharp-angle", type=float, default=60.0,
                   help="edges folding by more than this many degrees pin the field to their direction; 0 disables")
    p.add_argument("--symmetry", default="auto", choices=("auto", "none", "x", "y", "z"),
                   help="mirror plane the field is made square to; auto reads the mesh's X symmetry setting")
    p.add_argument("--hard", type=float, default=50.0, help="weight of a pinned vertex, against align times "
                                                              "confidence (at most align) on a free one")
    p.add_argument("--pin-all", action="store_true",
                   help="hand the engine the field at full weight everywhere, so it keeps it as is instead of "
                        "re-solving around the confident vertices")
    p.add_argument("--engine", action="store_true",
                   help="also run the kit's engine on the proxy, free and steered by each field, project the "
                        "results onto the source and count pinch points (needs Sculpt Kit enabled in this "
                        "Blender with its own engine wheel: run through the bench profile, see tools/bench_remesh.sh)")
    p.add_argument("--shape", default="sphere", choices=("sphere", "capsule"),
                   help="without a .blend: a noisy sphere (no true curvature direction: alignment can only "
                        "add singularities) or a noisy capsule 3 times as long as wide (alignment should "
                        "follow its axis)")
    return p.parse_args(argv)


# ------------------------------------------------------------------------------------------------ mesh access

def coordinates(mesh):
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.attributes["position"].data.foreach_get("vector", co)
    return co.reshape(-1, 3)


def triangles(mesh):
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    return tris.reshape(-1, 3)


def noisy_shape(faces, shape):
    import bmesh
    subdivisions = int(np.ceil(np.log(faces / 20) / np.log(4) - 0.01)) + 1  # at least this many faces: 1 is the icosahedron
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=1.0)
    mesh = bpy.data.meshes.new("noisy " + shape)
    bm.to_mesh(mesh)
    bm.free()
    co = coordinates(mesh)
    if shape == "capsule":  # the two hemispheres pulled apart along z, the band between them stretched
        co[:, 2] += np.clip(co[:, 2] * 4.0, -1.0, 1.0)
    edge = 1.0 / (2 ** subdivisions)
    co += np.random.default_rng(0).standard_normal(co.shape) * 0.4 * edge
    mesh.attributes["position"].data.foreach_set("vector", co.astype(np.float32).ravel())
    mesh.update()
    ob = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def proxy_of(ob, faces, method="decimate"):
    """A coarser copy of the object as a new mesh object: decimated (collapse, keeps the shape, makes slivers),
    or voxel remeshed (even faces, rounds off detail finer than the voxel, which is what the field wants)."""
    copy = ob.copy()
    copy.data = ob.data.copy()
    copy.name = "proxy"
    bpy.context.scene.collection.objects.link(copy)
    if method == "voxel":
        area = np.empty(len(copy.data.polygons), dtype=np.float32)
        copy.data.polygons.foreach_get("area", area)
        copy.data.remesh_voxel_size = (1.4 * float(area.sum()) / faces) ** 0.5  # the kit's FACES_PER_VOXEL_AREA
        copy.data.use_remesh_preserve_attributes = False
        with bpy.context.temp_override(object=copy, active_object=copy, selected_objects=[copy]):
            bpy.ops.object.voxel_remesh()
        return copy
    ratio = min(1.0, faces / max(1, len(copy.data.polygons)))
    if ratio < 1.0:
        mod = copy.modifiers.new("decimate", 'DECIMATE')
        mod.ratio = ratio
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = bpy.data.meshes.new_from_object(copy.evaluated_get(depsgraph))
        copy.modifiers.remove(mod)
        old = copy.data
        copy.data = evaluated
        bpy.data.meshes.remove(old)
    return copy


# ------------------------------------------------------------------------------------------------ geometry

def unique_edges(tris):
    pairs = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    pairs.sort(axis=1)
    edges, inverse = np.unique(pairs, axis=0, return_inverse=True)
    return edges, inverse.reshape(3, -1).T  # edge id per triangle side (01, 12, 20)


def taubin(co, edges, target_faces, area, strength):
    count = np.bincount(edges.ravel(), minlength=len(co))
    edge = float(np.median(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)))
    quad = (area / target_faces) ** 0.5
    steps = int(min(40, strength * (quad / edge) ** 2))

    def mean(x):
        total = np.empty_like(x)
        for a in range(3):
            total[:, a] = (np.bincount(edges[:, 0], weights=x[edges[:, 1], a], minlength=len(x))
                           + np.bincount(edges[:, 1], weights=x[edges[:, 0], a], minlength=len(x)))
        return total / np.maximum(count, 1)[:, None]
    co = co.copy()
    for _ in range(steps):
        co += 0.5 * (mean(co) - co)
        co -= 0.53 * (mean(co) - co)
    return co, steps


def vertex_normals_and_areas(co, tris):
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
    """Per edge, the sum of the cotangents of the two angles opposite it (clamped to stay positive)."""
    weights = np.zeros(edge_of_side.max() + 1)
    for side, opposite in ((0, 2), (1, 0), (2, 1)):  # side 01 is opposite corner 2, and so on
        i, j, k = tris[:, (side) % 3], tris[:, (side + 1) % 3], tris[:, opposite]
        u, v = co[i] - co[k], co[j] - co[k]
        cot = (u * v).sum(1) / np.maximum(np.linalg.norm(np.cross(u, v), axis=1), 1e-20)
        weights += np.bincount(edge_of_side[:, side], weights=cot, minlength=len(weights))
    return np.maximum(weights * 0.5, 1e-4)


def tangent_bases(co, normals, edges):
    """An orthonormal tangent basis (x, y) at each vertex, x along its first edge projected to the plane."""
    first = np.full(len(co), -1, dtype=np.int64)
    first[edges[::-1, 0]] = edges[::-1, 1]  # the last write wins: any neighbour will do
    first[edges[::-1, 1]] = np.where(first[edges[::-1, 1]] < 0, edges[::-1, 0], first[edges[::-1, 1]])
    x = co[np.maximum(first, 0)] - co
    x -= (x * normals).sum(1)[:, None] * normals
    degenerate = np.linalg.norm(x, axis=1) < 1e-12
    x[degenerate] = np.cross(normals[degenerate], [1.0, 0.0, 0.0])
    x /= np.maximum(np.linalg.norm(x, axis=1), 1e-20)[:, None]
    return x, np.cross(normals, x)


def local_angle(vectors, tx, ty):
    return np.arctan2((vectors * ty).sum(1), (vectors * tx).sum(1))


def principal_directions(co, normals, edges, tx, ty):
    """Per vertex, the direction of largest curvature in its tangent basis (as an angle) and the anisotropy
    |k1 - k2| / (|k1| + |k2|): a least-squares shape operator from the one-ring's normal differences."""
    n = len(co)
    ata = np.zeros((n, 3))  # the symmetric 2x2 A^T A: xx, xy, yy
    atb = np.zeros((n, 4))  # A^T b for the two rows of S: (xx, xy) for S's first row, (yx, yy) for the second
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
    inv = np.stack([ata[:, 2], -ata[:, 1], ata[:, 0]], 1) / det[:, None]  # inverse of the 2x2
    s_xx = inv[:, 0] * atb[:, 0] + inv[:, 1] * atb[:, 1]
    s_xy = inv[:, 1] * atb[:, 0] + inv[:, 2] * atb[:, 1]
    s_yx = inv[:, 0] * atb[:, 2] + inv[:, 1] * atb[:, 3]
    s_yy = inv[:, 1] * atb[:, 2] + inv[:, 2] * atb[:, 3]
    # symmetrize, then the principal direction of a 2x2 symmetric matrix
    b = 0.5 * (s_xy + s_yx)
    theta = 0.5 * np.arctan2(2 * b, s_xx - s_yy)
    half_trace = 0.5 * (s_xx + s_yy)
    radius = np.sqrt(((s_xx - s_yy) * 0.5) ** 2 + b ** 2)
    k1, k2 = half_trace + radius, half_trace - radius
    return theta, k1, k2


def confidence_of(k1, k2, quad):
    """How much a vertex's principal direction means, 0 to 1: the difference of its curvatures as the angle
    (radians) the normal turns across one quad; half a radian is full confidence. On a sphere this is 0,
    so the ratio |k1 - k2| / (|k1| + |k2|) would be pure noise there."""
    return np.clip(np.abs(k1 - k2) * quad / 0.5, 0.0, 1.0)


def connection(co, edges, tx, ty, n_sym=4):
    """Transport along each edge i->j as a unit complex number raised to n_sym, for a field with that symmetry,
    and the plain (1-fold) transport, which the index count needs unwrapped."""
    v = co[edges[:, 1]] - co[edges[:, 0]]
    at_i = local_angle(v, tx[edges[:, 0]], ty[edges[:, 0]])
    at_j = local_angle(-v, tx[edges[:, 1]], ty[edges[:, 1]])
    # The edge leaves i at angle at_i and arrives at j pointing the opposite way, at_j + pi: the plain transport
    # carries that half turn. The 4-fold one does not notice it (exp(4 i pi) = 1), so it is the same either way.
    r1 = np.exp(1j * (at_j - at_i + np.pi))
    return r1 ** n_sym, r1


def laplacian_apply(z, edges, weights, r):
    """(L z)_i = sum_j w_ij (z_i - transport_ji z_j), the connection Laplacian, through bincount."""
    i, j = edges[:, 0], edges[:, 1]
    from_j = weights * (z[i] - np.conj(r) * z[j])  # j's value carried to i
    from_i = weights * (z[j] - r * z[i])
    out = np.zeros(len(z), dtype=np.complex128)
    for part, sign in ((from_j, 1.0), (from_i, 1.0)):
        pass
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


def smoothest_field(edges, weights, r, mass, rounds=20):
    """The smoothest field: the lowest generalized eigenvector of L, by inverse power iteration."""
    z = np.exp(1j * np.random.default_rng(0).uniform(0, 2 * np.pi, len(mass)))
    shift = 1e-4 * mass.mean()
    for _ in range(rounds):
        z, _ = conjugate_gradient(lambda x: laplacian_apply(x, edges, weights, r) + shift * mass * x, mass * z, z, iterations=200)
        z /= np.sqrt(np.vdot(z, mass * z).real)
    return z


def aligned_field(edges, weights, r, mass, d, weight, start):
    """Minimizes the Dirichlet energy plus sum m_i w_i |z_i - d_i|^2: w is align times the curvature confidence
    on free vertices, and the hard weight on constrained ones (sharp edges, the symmetry plane)."""
    diag = mass * weight
    z, steps = conjugate_gradient(lambda x: laplacian_apply(x, edges, weights, r) + diag * x + 1e-6 * mass * x,
                                  diag * d, start)
    return z, steps


def sharp_constraints(co, tris, edges, edge_of_side, tx, ty, angle_degrees):
    """Vertices on edges where the surface folds by more than the angle, each asked to align with that edge
    (a vertex on several sharp edges takes the last one; corners of three are rare on a sculpt). Measured on
    the unsmoothed proxy, since smoothing is what removes creases. Returns the direction field and a mask."""
    n = len(co)
    pinned = np.zeros(n, dtype=bool)
    d = np.zeros(n, dtype=np.complex128)
    if angle_degrees <= 0.0:
        return d, pinned
    a, b, c = co[tris[:, 0]], co[tris[:, 1]], co[tris[:, 2]]
    face_normals = np.cross(b - a, c - a)
    face_normals /= np.maximum(np.linalg.norm(face_normals, axis=1), 1e-20)[:, None]
    # the two faces of each edge: every manifold edge appears exactly twice among the triangle sides
    order = np.argsort(edge_of_side.ravel(), kind="stable")
    edge_ids = edge_of_side.ravel()[order]
    tri_ids = np.repeat(np.arange(len(tris)), 3)[order]
    first = np.flatnonzero(np.r_[True, edge_ids[1:] != edge_ids[:-1]])
    counts = np.diff(np.r_[first, len(edge_ids)])
    two = first[counts == 2]
    cos_fold = (face_normals[tri_ids[two]] * face_normals[tri_ids[two + 1]]).sum(1)
    sharp = edge_ids[two][cos_fold < np.cos(np.radians(angle_degrees))]
    for end in (0, 1):
        v = edges[sharp, end]
        direction = co[edges[sharp, 1 - end]] - co[v]
        d[v] = np.exp(4j * local_angle(direction, tx[v], ty[v]))
        pinned[v] = True
    return d, pinned


def symmetry_constraints(co, normals, tx, ty, quad, axis=0):
    """Vertices within one quad of the mirror plane, asked to align with the plane's normal (so quads meet the
    seam square-on and a mirrored half welds to a clean loop). Returns the direction field and a mask."""
    n = len(co)
    pinned = np.abs(co[:, axis]) < quad
    d = np.zeros(n, dtype=np.complex128)
    plane_normal = np.zeros((n, 3))
    plane_normal[:, axis] = 1.0
    plane_normal -= (plane_normal * normals).sum(1)[:, None] * normals  # into the tangent plane
    usable = pinned & (np.linalg.norm(plane_normal, axis=1) > 1e-6)
    d[usable] = np.exp(4j * local_angle(plane_normal[usable], tx[usable], ty[usable]))
    return d, usable


def face_indices(z, tris, edge_of_side, edges, r, r1, n_sym=4):
    """The index of each face: the field's rotation around it (in units of 2 pi of the n-fold field) plus the
    connection's own holonomy, so a parallel field on a sphere still sums to n_sym times its Euler number.
    The holonomy is taken from the 1-fold transport and multiplied by n_sym: taken from the n-fold one it would
    wrap whenever a face turns by more than 180 / n_sym degrees, which slivers on a decimated proxy do."""
    unit = z / np.maximum(np.abs(z), 1e-20)
    total = np.zeros(len(tris))
    holonomy = np.ones(len(tris), dtype=np.complex128)
    for side in range(3):
        a, b = tris[:, side], tris[:, (side + 1) % 3]
        e = edge_of_side[:, side]
        forward = edges[e, 0] == a
        transport = np.where(forward, r[e], np.conj(r[e]))
        total += np.angle(unit[b] * np.conj(transport * unit[a]))
        holonomy *= np.where(forward, r1[e], np.conj(r1[e]))
    total += n_sym * np.angle(holonomy)
    return np.rint(total / (2 * np.pi)).astype(int)


# ------------------------------------------------------------------------------------------------ output

def field_vectors(theta, tx, ty, n_sym=4):
    """The angle of a 4-fold field value (as a complex number's argument) back to a 3D tangent direction."""
    return np.cos(theta)[:, None] * tx + np.sin(theta)[:, None] * ty


def kit_modules():
    """The enabled Sculpt Kit's remesh modules, or None."""
    import importlib
    name = next((k for k in bpy.context.preferences.addons.keys() if k.endswith("sculpt_kit")), None)
    if name is None:
        return None, None
    try:
        return (importlib.import_module(name + ".features.remesh"),
                importlib.import_module(name + ".features.remesh_instant"))
    except ImportError:
        return None, None


def quad_axis_alignment(mesh, remesh):
    """For the capsule: cos of 4 times the angle between each edge in the middle band and the axis, 1 when the
    quads run along and around the axis, -1 when they run diagonally."""
    co = coordinates(mesh)
    edges = remesh._edges(mesh)
    mid = 0.5 * (co[edges[:, 0]] + co[edges[:, 1]])
    band = np.abs(mid[:, 2]) < 0.8
    e = co[edges[:, 1]] - co[edges[:, 0]]
    e /= np.maximum(np.linalg.norm(e, axis=1), 1e-20)[:, None]
    radial = mid.copy()
    radial[:, 2] = 0.0
    radial /= np.maximum(np.linalg.norm(radial, axis=1), 1e-20)[:, None]
    around = np.cross(radial, [0.0, 0.0, 1.0])
    angle = np.arctan2((e * around).sum(1), e[:, 2])
    return float(np.cos(4 * angle)[band].mean()) if band.any() else None


def run_engine(name, proxy, source, target, remesh, instant, directions=None, confidence=None, is_capsule=False):
    """The kit's engine on a copy of the proxy, steered by the field when given, projected onto the source."""
    from mathutils.bvhtree import BVHTree
    started = time.perf_counter()
    mesh = proxy.data.copy()
    mesh.name = name
    stats = instant.remesh(mesh, target, pure_quad=True, orientation=directions, orientation_weight=confidence)
    surface = BVHTree.FromObject(source, bpy.context.evaluated_depsgraph_get())
    remesh._project_and_relax(mesh, surface)
    unfolded = remesh._unfold(mesh, surface)
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    row = {"seconds": round(time.perf_counter() - started, 2), "faces": len(mesh.polygons),
           "singularities": stats.get("singularities"), "pinch_points": remesh._pinch_points(mesh),
           "unfolded": unfolded, "open_edges": remesh._open_edges(mesh)}
    if is_capsule:
        row["quad_axis_alignment"] = quad_axis_alignment(mesh, remesh)
    return row


def save_field_object(name, co, directions, length):
    """Short line segments along the field at every vertex, as a mesh object."""
    verts = np.concatenate([co - directions * length * 0.5, co + directions * length * 0.5])
    edges = np.stack([np.arange(len(co)), np.arange(len(co)) + len(co)], 1)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts.tolist(), edges.tolist(), [])
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def main():
    args = parse()
    os.makedirs(args.out, exist_ok=True)
    if args.object:
        source = bpy.data.objects[args.object]
    elif bpy.data.filepath:
        source = next(o for o in bpy.context.scene.objects if o.type == 'MESH')
    else:
        source = noisy_shape(args.sphere_faces, args.shape)
    source.modifiers.clear()  # the field is for the mesh itself, not a subdivided or decimated view of it
    print("FIELD source %s: %d faces" % (source.name, len(source.data.polygons)), flush=True)

    started = time.perf_counter()
    proxy = proxy_of(source, args.proxy, args.proxy_method)
    co, tris = coordinates(proxy.data), triangles(proxy.data)
    edges, edge_of_side = unique_edges(tris)
    normals, mass, area = vertex_normals_and_areas(co, tris)
    smooth, steps = taubin(co, edges, args.target, area, args.smoothing)
    normals, mass, _ = vertex_normals_and_areas(smooth, tris)
    print("FIELD proxy %d faces, %d vertices, Euler characteristic %d (index sum should be 4x that), smoothed %d steps, %.1fs"
          % (len(tris), len(co), len(co) - len(edges) + len(tris), steps, time.perf_counter() - started), flush=True)

    started = time.perf_counter()
    quad = (area / args.target) ** 0.5
    tx, ty = tangent_bases(smooth, normals, edges)
    theta, k1, k2 = principal_directions(smooth, normals, edges, tx, ty)
    anisotropy = confidence_of(k1, k2, quad)
    weights = cotan_weights(smooth, tris, edge_of_side)
    r, r1 = connection(smooth, edges, tx, ty)
    d = np.exp(4j * theta)
    print("FIELD setup %.1fs; confidence median %.2f, mean %.2f, share above 0.5: %.2f"
          % (time.perf_counter() - started, np.median(anisotropy), anisotropy.mean(), (anisotropy > 0.5).mean()), flush=True)

    # Constraints: the field is told where to go on sharp edges and along the symmetry plane, hard.
    pinned = np.zeros(len(co), dtype=bool)
    sharp_d, sharp_mask = sharp_constraints(co, tris, edges, edge_of_side, tx, ty, args.sharp_angle)
    d[sharp_mask], pinned[sharp_mask] = sharp_d[sharp_mask], True
    symmetry = args.symmetry
    if symmetry == "auto":
        symmetry = "x" if source.data.use_mirror_x else "none"
    if symmetry != "none":
        mirror_d, mirror_mask = symmetry_constraints(smooth, normals, tx, ty, quad, "xyz".index(symmetry))
        d[mirror_mask], pinned[mirror_mask] = mirror_d[mirror_mask], True
    print("FIELD constraints: %d vertices on sharp edges (over %g degrees), %d on the symmetry plane (%s), hard weight %g"
          % (int(sharp_mask.sum()), args.sharp_angle, int(pinned.sum() - sharp_mask.sum()), symmetry, args.hard), flush=True)

    results = []
    z = None
    remesh_module = instant_module = None
    if args.engine:
        remesh_module, instant_module = kit_modules()
        if instant_module is None or not instant_module.available():
            print("FIELD engine: Sculpt Kit with its engine is not enabled in this Blender; skipping", flush=True)
            instant_module = None
        else:
            is_capsule = args.shape == "capsule" and not bpy.data.filepath
            row = run_engine("engine free", proxy, source, args.target, remesh_module, instant_module, is_capsule=is_capsule)
            print("FIELD engine free " + json.dumps(row), flush=True)
            results.append({"engine": "free", **row})
    for align in (float(a) for a in args.align.split(",")):
        started = time.perf_counter()
        if align <= 0.0:
            z, steps = smoothest_field(edges, weights, r, mass), 0
        else:
            weight = align * anisotropy
            weight[pinned] = args.hard
            z, steps = aligned_field(edges, weights, r, mass, d, weight, z if z is not None else d.copy())
        index = face_indices(z, tris, edge_of_side, edges, r, r1)
        singular = int((index != 0).sum())
        row = {"align": align, "seconds": round(time.perf_counter() - started, 2), "cg_steps": steps,
               "singularities": singular, "index_sum": int(index.sum()),
               "index_counts": {int(k): int(c) for k, c in zip(*np.unique(index, return_counts=True))}}
        if args.shape == "capsule" and not bpy.data.filepath:
            # How well the field follows the capsule's axis in its middle band: cos of 4 times the angle
            # between the field and the axis projected onto the tangent plane, 1 when aligned, -1 at 45 degrees.
            band = np.abs(smooth[:, 2]) < 0.8
            axis = np.repeat(np.float64([[0.0, 0.0, 1.0]]), len(smooth), axis=0)
            axis -= (axis * normals).sum(1)[:, None] * normals
            axis_angle = local_angle(axis, tx, ty)
            row["axis_alignment"] = float(np.cos(4 * (np.angle(z) / 4.0 - axis_angle))[band].mean())
        results.append(row)
        print("FIELD " + json.dumps(row), flush=True)
        directions = field_vectors(np.angle(z) / 4.0, tx, ty)
        save_field_object("field align %g" % align, smooth, directions, quad * 0.8)
        if instant_module is not None and instant_module.available():
            steered = run_engine("engine align %g" % align, proxy, source, args.target, remesh_module, instant_module,
                                 directions.astype(np.float32),
                                 np.ones(len(co), dtype=np.float32) if args.pin_all else anisotropy.astype(np.float32),
                                 is_capsule=args.shape == "capsule" and not bpy.data.filepath)
            print("FIELD engine align %g " % align + json.dumps(steered), flush=True)
            results.append({"engine": "align %g" % align, **steered})
        np.savez(os.path.join(args.out, "field_align_%g.npz" % align), vertices=smooth.astype(np.float32),
                 faces=tris, direction=directions.astype(np.float32), confidence=anisotropy.astype(np.float32),
                 singular_faces=np.flatnonzero(index != 0))
    proxy.data.attributes.new("anisotropy", 'FLOAT', 'POINT').data.foreach_set("value", anisotropy.astype(np.float32))
    with open(os.path.join(args.out, "field.json"), "w") as f:
        json.dump({"source": source.name, "proxy_faces": len(tris), "target": args.target, "results": results}, f, indent=1)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(os.path.join(args.out, "field.blend")), copy=True)
    print("FIELD saved %s" % os.path.join(args.out, "field.blend"), flush=True)


main()
