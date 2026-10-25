"""Smoke test for the native engine: a noisy sphere remeshes to about the asked count, as quads, closed.

    python native/tests/smoke.py          (after pip install of the wheel, or with build/.../ on sys.path)
"""
import sys
import time

import numpy as np

import sculpt_kit_remesh


def uvsphere(nu=400, nv=200):
    u = np.linspace(0, 2 * np.pi, nu, endpoint=False)
    v = np.linspace(0.01, np.pi - 0.01, nv)
    U, V = np.meshgrid(u, v)
    P = np.stack([np.sin(V) * np.cos(U), np.sin(V) * np.sin(U), np.cos(V)], -1).reshape(-1, 3)
    idx = np.arange(nu * nv).reshape(nv, nu)
    f = []
    for i in range(nv - 1):
        a, b = idx[i], idx[i + 1]
        an, bn = np.roll(a, -1), np.roll(b, -1)
        f.append(np.stack([a, b, bn], -1))
        f.append(np.stack([a, bn, an], -1))
    return P.astype(np.float32), np.concatenate(f).astype(np.int32)


def octasphere(levels=6):
    """A closed unit sphere: an octahedron split in four `levels` times (QuadriFlow wants a closed mesh)."""
    V = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], float)
    F = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]])
    for _ in range(levels):
        middle, verts, faces = {}, list(V), []

        def mid(a, b):
            key = (min(a, b), max(a, b))
            if key not in middle:
                middle[key] = len(verts)
                verts.append((verts[a] + verts[b]) / 2)
            return middle[key]
        for a, b, c in F:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            faces += [[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]]
        V, F = np.array(verts), np.array(faces)
    return (V / np.linalg.norm(V, axis=1)[:, None]).astype(np.float32), F.astype(np.int32)


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("  [%s]" % detail if detail != "" else ""))
    return ok


def main():
    print("engine", sculpt_kit_remesh.version())
    V, F = uvsphere()
    V += (np.random.default_rng(0).standard_normal(V.shape) * 0.001).astype(np.float32)
    started = time.perf_counter()
    out = sculpt_kit_remesh.remesh(V, F, target_faces=4000, pure_quad=True, deterministic=True)
    seconds = time.perf_counter() - started
    sizes, corners, verts = out["sizes"], out["corners"], out["vertices"]
    ok = check("returns faces", len(sizes) > 0)
    ok &= check("pure quads", bool((sizes == 4).all()), str(np.unique(sizes)))
    ok &= check("about the asked count", 2000 < len(sizes) < 8000, len(sizes))
    ok &= check("indices in range", int(corners.max()) < len(verts) and int(corners.min()) >= 0)
    ok &= check("fast", seconds < 5.0, "%.2fs" % seconds)
    # a constrained run: every vertex asked to follow +Z, which the solver takes as a tangent direction
    out2 = sculpt_kit_remesh.remesh(V, F, target_faces=4000, orientation=np.tile(np.float32([0, 0, 1]), (len(V), 1)),
                                    orientation_weight=np.ones(len(V), dtype=np.float32))
    ok &= check("constrained run returns faces", len(out2["sizes"]) > 0)
    print("engine seconds: %.2f, singularities: %d" % (out["seconds"], out["singularities"]))
    # QuadriFlow, free and frozen to a cube-like field (8 corners, the fewest a sphere allows)
    V, F = octasphere()
    out3 = sculpt_kit_remesh.quadriflow(V, F, target_faces=2000)
    ok &= check("quadriflow returns quads", len(out3["quads"]) > 0 and int(out3["quads"].max()) < len(out3["vertices"]))
    unit = V
    axis = np.eye(3, dtype=np.float32)[(np.abs(unit).argmax(1) + 1) % 3]
    cube = (axis - (axis * unit).sum(1)[:, None] * unit).astype(np.float32)
    out4 = sculpt_kit_remesh.quadriflow(V, F, target_faces=2000, orientation=cube)
    valence = np.bincount(out4["quads"].ravel(), minlength=len(out4["vertices"]))
    ok &= check("quadriflow keeps a frozen field", int((valence != 4).sum()) <= 12,
          str({int(k): int(c) for k, c in zip(*np.unique(valence, return_counts=True)) if k != 4}))
    # two sizes per vertex: quads asked twice as long along the field as across it come out longer along it
    # (not twice: a uniform stretch is more than the field's lines can carry around a sphere)
    def stretch(out):
        P, Q = out["vertices"], out["quads"]
        a, b = P[Q[:, 1]] - P[Q[:, 0]], P[Q[:, 3]] - P[Q[:, 0]]
        d = cube[np.argmax(P[Q[:, 0]] @ V.T, axis=1)]
        along = np.abs((a * d).sum(1)) / np.linalg.norm(a, axis=1) > 0.7
        la, lb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
        return float(np.median(np.where(along, la / lb, lb / la)))
    asked = np.tile(np.array([[2.0 ** 0.5, 2.0 ** -0.5]], dtype=np.float32), (len(V), 1))
    out5 = sculpt_kit_remesh.quadriflow(V, F, target_faces=2000, orientation=cube, scale=asked)
    plain, stretched = stretch(out4), stretch(out5)
    ok &= check("quadriflow stretches quads along the field", stretched > 1.2 * plain, "%.2f, unstretched %.2f" % (stretched, plain))
    sys.exit(0 if ok else 1)


main()
