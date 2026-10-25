"""Close the Option C loop: feed a field from tools/field_prototype.py into the native engine.

    python native/tests/field_to_engine.py tests/out/field/field_align_1.npz [--target 20000] [--out DIR]

Runs the engine on the prototype's proxy twice, free and constrained to the field, and compares the
orientation singularities it reports and the valence of the quads it makes. Writes <out>/free.obj and
<out>/aligned.obj for looking at in Blender (File > Import > Wavefront).
"""
import argparse
import os

import numpy as np

import sculpt_kit_remesh


def valence_report(vertices, corners, sizes):
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
    pairs = []
    for n in np.unique(sizes):
        rows = corners[starts[sizes == n][:, None] + np.arange(n)]
        pairs.append(np.concatenate([np.stack([rows[:, i], rows[:, (i + 1) % n]], 1) for i in range(n)]))
    edges = np.sort(np.concatenate(pairs), 1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    valence = np.bincount(unique.ravel(), minlength=len(vertices))
    return {"faces": int(len(sizes)), "sides": {int(n): int(c) for n, c in zip(*np.unique(sizes, return_counts=True))},
            "valence": {int(v): int(c) for v, c in enumerate(np.bincount(valence[valence > 0])) if c},
            "pinch_points": int((valence >= 6).sum()), "open_edges": int((counts == 1).sum()),
            "nonmanifold_edges": int((counts > 2).sum())}


def write_obj(path, vertices, corners, sizes):
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
    with open(path, "w") as f:
        np.savetxt(f, vertices, fmt="v %.6f %.6f %.6f")
        for start, size in zip(starts, sizes):
            f.write("f " + " ".join(str(i + 1) for i in corners[start:start + size]) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("field")
    p.add_argument("--target", type=int, default=20000)
    p.add_argument("--out", default="tests/out/field_engine")
    args = p.parse_args()
    os.makedirs(args.out, exist_ok=True)
    data = np.load(args.field)
    vertices, faces = data["vertices"], data["faces"].astype(np.int32)
    print("proxy: %d vertices, %d faces" % (len(vertices), len(faces)))
    for name, extra in (("free", {}), ("aligned", {"orientation": data["direction"], "orientation_weight": data["confidence"]})):
        out = sculpt_kit_remesh.remesh(vertices, faces, target_faces=args.target, pure_quad=True, deterministic=True, **extra)
        report = valence_report(out["vertices"], out["corners"], out["sizes"])
        print("%-8s %.2fs  orientation singularities %d  %s" % (name, out["seconds"], out["singularities"], report))
        write_obj(os.path.join(args.out, name + ".obj"), out["vertices"], out["corners"], out["sizes"])


main()
