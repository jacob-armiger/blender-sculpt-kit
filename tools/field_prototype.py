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
    p.add_argument("--align", default="0,1,10")
    p.add_argument("--smoothing", type=float, default=1.0)
    p.add_argument("--out", default="tests/out/field")
    p.add_argument("--sphere-faces", type=int, default=1_300_000)
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
    subdivisions = int(round(np.log(faces / 20) / np.log(4)))
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


def proxy_of(ob, faces):
    """A decimated copy of the object as a new mesh object."""
    copy = ob.copy()
    copy.data = ob.data.copy()
    copy.name = "proxy"
    bpy.context.scene.collection.objects.link(copy)
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
    """Transport along each edge i->j as a unit complex number raised to n_sym, for a field with that symmetry."""
    v = co[edges[:, 1]] - co[edges[:, 0]]
    at_i = local_angle(v, tx[edges[:, 0]], ty[edges[:, 0]])
    at_j = local_angle(-v, tx[edges[:, 1]], ty[edges[:, 1]])
    return np.exp(1j * n_sym * (at_j - at_i))


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


def aligned_field(edges, weights, r, mass, d, confidence, align, start):
    """Minimizes the Dirichlet energy plus align * sum m_i c_i |z_i - d_i|^2."""
    diag = align * mass * confidence
    z, steps = conjugate_gradient(lambda x: laplacian_apply(x, edges, weights, r) + diag * x + 1e-6 * mass * x,
                                  diag * d, start)
    return z, steps


def face_indices(z, tris, edge_of_side, edges, r, n_sym=4):
    """The index of each face: the field's rotation around it (in units of 2 pi of the n-fold field) plus the
    connection's own holonomy, so a parallel field on a sphere still sums to n_sym times its Euler number."""
    unit = z / np.maximum(np.abs(z), 1e-20)
    total = np.zeros(len(tris))
    holonomy = np.ones(len(tris), dtype=np.complex128)
    for side in range(3):
        a, b = tris[:, side], tris[:, (side + 1) % 3]
        e = edge_of_side[:, side]
        forward = edges[e, 0] == a
        transport = np.where(forward, r[e], np.conj(r[e]))
        total += np.angle(unit[b] * np.conj(transport * unit[a]))
        holonomy *= transport
    total += np.angle(holonomy)
    return np.rint(total / (2 * np.pi)).astype(int)


# ------------------------------------------------------------------------------------------------ output

def field_vectors(theta, tx, ty, n_sym=4):
    """The angle of a 4-fold field value (as a complex number's argument) back to a 3D tangent direction."""
    return np.cos(theta)[:, None] * tx + np.sin(theta)[:, None] * ty


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
    print("FIELD source %s: %d faces" % (source.name, len(source.data.polygons)), flush=True)

    started = time.perf_counter()
    proxy = proxy_of(source, args.proxy)
    co, tris = coordinates(proxy.data), triangles(proxy.data)
    edges, edge_of_side = unique_edges(tris)
    normals, mass, area = vertex_normals_and_areas(co, tris)
    smooth, steps = taubin(co, edges, args.target, area, args.smoothing)
    normals, mass, _ = vertex_normals_and_areas(smooth, tris)
    print("FIELD proxy %d faces, %d vertices, smoothed %d steps, %.1fs" % (len(tris), len(co), steps, time.perf_counter() - started), flush=True)

    started = time.perf_counter()
    quad = (area / args.target) ** 0.5
    tx, ty = tangent_bases(smooth, normals, edges)
    theta, k1, k2 = principal_directions(smooth, normals, edges, tx, ty)
    anisotropy = confidence_of(k1, k2, quad)
    weights = cotan_weights(smooth, tris, edge_of_side)
    r = connection(smooth, edges, tx, ty)
    d = np.exp(4j * theta)
    print("FIELD setup %.1fs; confidence median %.2f, mean %.2f, share above 0.5: %.2f"
          % (time.perf_counter() - started, np.median(anisotropy), anisotropy.mean(), (anisotropy > 0.5).mean()), flush=True)

    results = []
    z = None
    for align in (float(a) for a in args.align.split(",")):
        started = time.perf_counter()
        if align <= 0.0:
            z, steps = smoothest_field(edges, weights, r, mass), 0
        else:
            z, steps = aligned_field(edges, weights, r, mass, d, anisotropy, align, z if z is not None else d.copy())
        index = face_indices(z, tris, edge_of_side, edges, r)
        singular = int((index != 0).sum())
        row = {"align": align, "seconds": round(time.perf_counter() - started, 2), "cg_steps": steps,
               "singularities": singular, "index_sum": int(index.sum()),
               "index_counts": {int(k): int(c) for k, c in zip(*np.unique(index, return_counts=True))}}
        results.append(row)
        print("FIELD " + json.dumps(row), flush=True)
        directions = field_vectors(np.angle(z) / 4.0, tx, ty)
        save_field_object("field align %g" % align, smooth, directions, quad * 0.8)
        np.savez(os.path.join(args.out, "field_align_%g.npz" % align), vertices=smooth.astype(np.float32),
                 faces=tris, direction=directions.astype(np.float32), confidence=anisotropy.astype(np.float32),
                 singular_faces=np.flatnonzero(index != 0))
    proxy.data.attributes.new("anisotropy", 'FLOAT', 'POINT').data.foreach_set("value", anisotropy.astype(np.float32))
    with open(os.path.join(args.out, "field.json"), "w") as f:
        json.dump({"source": source.name, "proxy_faces": len(tris), "target": args.target, "results": results}, f, indent=1)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(os.path.join(args.out, "field.blend")), copy=True)
    print("FIELD saved %s" % os.path.join(args.out, "field.blend"), flush=True)


main()
