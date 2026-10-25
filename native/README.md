# native: Sculpt Kit's own remesh engine

The engine behind fRemesher: the Instant Meshes library (BSD-3) and QuadriFlow (BSD-3) compiled into a Python
module that takes numpy arrays and returns polygons, with a hook to hand it our own orientation field (the
curvature-aligned field of `sculpt_kit/features/remesh_field.py`).

```
native/
  pyproject.toml                scikit-build-core + nanobind; cibuildwheel settings (one stable-ABI wheel per platform)
  CMakeLists.txt                fetches Instant Meshes (pinned) and Eigen 3.4 at configure time, builds its old TBB fork
  src/engine.cpp                the binding: remesh(vertices, faces, ...) in memory, optional orientation constraints
  src/sculpt_kit_remesh/        the Python package around it
  tests/smoke.py                a noisy sphere remeshes to quads, closed, in seconds
```

## Build here (macOS)

A venv made from Blender's own Python has what the build needs (cmake, ninja, nanobind, scikit-build-core):

```bash
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 -m venv /tmp/skr-build && /tmp/skr-build/bin/pip install cmake ninja nanobind scikit-build-core numpy
```

```bash
/tmp/skr-build/bin/pip wheel native/ -w native/wheelhouse --no-deps
```

The first configure downloads Instant Meshes and Eigen (about 10 MB). Then:

```bash
/tmp/skr-build/bin/pip install native/wheelhouse/sculpt_kit_remesh-*.whl && /tmp/skr-build/bin/python native/tests/smoke.py
```

## Wheels for all platforms

`.github/workflows/native-wheels.yml` runs cibuildwheel on macOS arm64, Windows x64 and Linux x86_64 and
uploads a wheel per platform. Copy them into `sculpt_kit/wheels/`; `blender_manifest.toml` lists them by
filename. Wheels are stable-ABI (`cp312-abi3`), so Blender 5.2's
Python 3.13 and later versions load the same file.

## API

```python
out = sculpt_kit_remesh.remesh(
    vertices,                 # (n, 3) float32
    triangles,                # (m, 3) int32, counter-clockwise
    target_faces=20000,       # about this many faces (pure quads: the count after the final subdivision)
    rosy=4, posy=4,           # 4/4 for quads; 6/3 for triangles
    crease_angle=-1.0,        # degrees; -1 disables
    extrinsic=False, align_to_boundaries=False,
    smooth_iterations=2,      # Instant Meshes' own smoothing and reprojection passes
    pure_quad=True,           # subdivide the quad-dominant result into quads (4x faces: the target is divided by 4 first)
    deterministic=True,
    orientation=None,         # (n, 3) float32: a direction per input vertex for the orientation field to follow
    orientation_weight=None,  # (n,) float32 in 0..1: 0 leaves the vertex free, 1 pins it
    quiet=True)               # swallow the library's console chatter
out["vertices"], out["corners"], out["sizes"], out["singularities"], out["seconds"]

out = sculpt_kit_remesh.quadriflow(
    vertices, triangles,      # as above
    target_faces=20000,       # pure quads, about this many
    orientation=None,         # (n, 3) float32: freezes the orientation field to these directions
    preserve_sharp=False, preserve_boundary=False, adaptive_scale=False, minimum_cost_flow=False, seed=0)
out["vertices"], out["quads"], out["singularities"], out["position_singularities"], out["seconds"]
```

QuadriFlow (Huang et al. 2018, BSD-3) is vendored from Blender's own copy in `third_party/quadriflow/`
(no Boost; see its README.sculptkit) and driven from `src/qflow_bridge.cpp`, a separate translation unit so
its headers never meet Instant Meshes'.

`orientation` is the hook for the form-following layout: a field computed elsewhere (curvature-aligned, constrained by sharp edges,
face sets and the symmetry plane) goes in as per-vertex tangent directions and weights; the hierarchy keeps
pinned vertices and smooths the rest. Vertices the library adds when it subdivides a coarse input get no
constraint.

## Known limits of the library

- Extraction's "Step 5: snapping and removing unnecessary edges" is a repeat-until-no-change loop over all
  vertices that can run for many minutes on a noisy 1.3M-face input at a 100k target. Smoothing the input to
  the target scale first (what fRemesher already does) avoids it in the cases measured.
- Instant Meshes has no seed, no symmetry and no attribute transfer; fRemesher's own projection and relaxation
  run after the engine.
