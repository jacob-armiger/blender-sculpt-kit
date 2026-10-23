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
    sys.exit(0 if ok else 1)


main()
