"""Benchmark the quad-remesh engines on a sculpt, headless.

Run through tools/bench_remesh.sh, which builds the kit, installs it into a throwaway profile and calls:

    blender -b <file.blend> --python tools/bench_remesh.py -- [--object NAME] [--targets 20000,100000]
                                                             [--engines QUADRIFLOW,INSTANT] [--out DIR]

With no .blend it makes a noisy 1.3M-face sphere. For every engine and target it runs Repairing Remesh on a
copy of the object and measures: time, face count, share of quads, pinch points (valence 6 or more),
valence histogram, open and non-manifold edges, and how far the result sits from the sculpt (nearest-point
distance and normal deviation, sampled). Results go to <out>/bench.json and <out>/bench.blend, where the
results stand in a row next to the original for judging by eye.
"""
import argparse
import json
import os
import sys
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

MODULE = "bl_ext.user_default.sculpt_kit"


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--object", default="")
    p.add_argument("--targets", default="20000,100000")
    p.add_argument("--engines", default="QUADRIFLOW,INSTANT")
    p.add_argument("--out", default=os.environ.get("SCULPT_KIT_BENCH_OUT", "tests/out/bench"))
    p.add_argument("--sphere-faces", type=int, default=1_300_000)
    return p.parse_args(argv)


def noisy_sphere(faces):
    """An icosphere of about this many faces, with brush-grain noise of 0.4 edge lengths."""
    import bmesh
    subdivisions = int(np.ceil(np.log(faces / 20) / np.log(4) - 0.01))  # at least this many faces
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=1.0)
    mesh = bpy.data.meshes.new("noisy sphere")
    bm.to_mesh(mesh)
    bm.free()
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.attributes["position"].data.foreach_get("vector", co)
    edge = 2.0 / (2 ** subdivisions) * 0.5  # about the icosphere's edge length
    co += np.random.default_rng(0).standard_normal(co.shape).astype(np.float32) * 0.4 * edge
    mesh.attributes["position"].data.foreach_set("vector", co)
    mesh.update()
    ob = bpy.data.objects.new("noisy sphere", mesh)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def edges_of(mesh):
    verts = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.attributes[".edge_verts"].data.foreach_get("value", verts)
    return verts.reshape(-1, 2)


def corner_edges(mesh):
    values = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.attributes[".corner_edge"].data.foreach_get("value", values)
    return values


def measure(ob, surface, samples=20000):
    """Quality numbers for a remeshed object against the original sculpt's BVH."""
    mesh = ob.data
    sizes = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", sizes)
    edges = edges_of(mesh)
    valence = np.bincount(edges.ravel(), minlength=len(mesh.vertices))
    uses = np.bincount(corner_edges(mesh), minlength=len(mesh.edges))
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.attributes["position"].data.foreach_get("vector", co)
    co = co.reshape(-1, 3)
    normals = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertex_normals.foreach_get("vector", normals)
    normals = normals.reshape(-1, 3)
    picked = np.random.default_rng(1).choice(len(co), size=min(samples, len(co)), replace=False)
    distance, deviation = [], []
    for i in picked:
        hit, normal, _index, dist = surface.find_nearest(Vector(co[i]))
        if hit is not None:
            distance.append(dist)
            deviation.append(np.degrees(np.arccos(np.clip(np.dot(normals[i], normal), -1.0, 1.0))))
    distance, deviation = np.array(distance), np.array(deviation)
    diagonal = float(np.linalg.norm(np.ptp(co, axis=0)))
    return {
        "faces": int(len(sizes)),
        "quads": float((sizes == 4).mean()) if len(sizes) else 0.0,
        "sides": {int(n): int(c) for n, c in zip(*np.unique(sizes, return_counts=True))},
        "valence": {int(v): int(c) for v, c in enumerate(np.bincount(valence)) if c},
        "pinch_points": int((valence >= 6).sum()),
        "open_edges": int((uses == 1).sum()),
        "nonmanifold_edges": int((uses > 2).sum()),
        "distance_mean_pct_of_diagonal": float(distance.mean() / diagonal * 100) if len(distance) else None,
        "distance_max_pct_of_diagonal": float(distance.max() / diagonal * 100) if len(distance) else None,
        "normal_deviation_mean_deg": float(deviation.mean()) if len(deviation) else None,
        "normal_deviation_p95_deg": float(np.percentile(deviation, 95)) if len(deviation) else None,
    }


def main():
    args = parse()
    os.makedirs(args.out, exist_ok=True)
    if args.object:
        ob = bpy.data.objects[args.object]
    elif bpy.data.filepath:
        ob = next(o for o in bpy.context.scene.objects if o.type == 'MESH')
    else:
        ob = noisy_sphere(args.sphere_faces)
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = ob
    surface = BVHTree.FromObject(ob, bpy.context.evaluated_depsgraph_get())
    width = float(np.ptp(np.array([v.co.x for v in ob.data.vertices]))) * 1.3 if len(ob.data.vertices) < 2_000_000 else 1.0
    width = max(width, 0.01)
    print("BENCH object %s: %d faces" % (ob.name, len(ob.data.polygons)), flush=True)

    results = []
    column = 1
    for engine in args.engines.split(","):
        for target in (int(t) for t in args.targets.split(",")):
            copy = ob.copy()
            copy.data = ob.data.copy()
            copy.name = "%s %dk" % (engine.title(), target // 1000)
            bpy.context.scene.collection.objects.link(copy)
            bpy.context.view_layer.objects.active = copy
            copy.select_set(True)
            started = time.perf_counter()
            try:
                with bpy.context.temp_override(object=copy, active_object=copy, selected_objects=[copy]):
                    outcome = bpy.ops.sculptkit.quad_remesh(target_faces=target, engine=engine, use_symmetry=False)
                error = ""
            except RuntimeError as e:
                outcome, error = {'CANCELLED'}, str(e).splitlines()[-1][:200]
            seconds = time.perf_counter() - started
            copy.select_set(False)
            row = {"engine": engine, "target": target, "seconds": round(seconds, 2), "finished": 'FINISHED' in outcome, "error": error}
            if 'FINISHED' in outcome:
                row.update(measure(copy, surface))
            results.append(row)
            print("BENCH " + json.dumps(row), flush=True)
            copy.location.x += width * column
            column += 1

    with open(os.path.join(args.out, "bench.json"), "w") as f:
        json.dump({"object": ob.name, "faces": len(ob.data.polygons), "results": results}, f, indent=1)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(os.path.join(args.out, "bench.blend")), copy=True)
    print("BENCH saved %s" % os.path.join(args.out, "bench.blend"), flush=True)


main()
