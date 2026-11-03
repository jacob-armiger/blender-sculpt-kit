# native: Sculpt Kit's own remesh engine

The engine behind fRemesher: QuadriFlow (BSD-3) and the Instant Meshes library (BSD-3) compiled into a Python module
that takes numpy arrays and returns polygons. fRemesher runs QuadriFlow, frozen to our own field and sizes (the
curvature-aligned field of `sculpt_kit/features/remesh_field.py`). Instant Meshes is no longer an engine choice; only
`lab/rig/field_prototype.py` still calls it.

```
native/
  pyproject.toml                scikit-build-core + nanobind; cibuildwheel settings (one stable-ABI wheel per platform)
  CMakeLists.txt                fetches Instant Meshes (pinned) and Eigen 3.4 at configure time, builds its old TBB fork
  src/engine.cpp                the binding: quadriflow(...) and remesh(...) in memory, optional orientation and sizes
  src/qflow_bridge.cpp          QuadriFlow's side of the binding, frozen to a given field
  src/sculpt_kit_remesh/        the Python package around it
  third_party/quadriflow/       QuadriFlow, from Blender's copy; our changes are in its README.sculptkit
  patches/                      Instant Meshes patches: the Step 5 cap, a per-vertex scale
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
out = sculpt_kit_remesh.quadriflow(
    vertices,                 # (n, 3) float32
    faces,                    # (m, 3) int32, counter-clockwise
    target_faces=20000,       # pure quads, about this many
    orientation=None,         # (n, 3) float32: freezes the orientation field to these directions
    scale=None,               # (n, 2): quad size along each direction and across it, relative to the uniform size
    preserve_sharp=False, preserve_boundary=False, adaptive_scale=False, minimum_cost_flow=False, seed=0,
    fill_holes=True)
out["vertices"], out["quads"], out["singularities"], out["position_singularities"], out["vertices_added"], out["seconds"]

out = sculpt_kit_remesh.remesh(  # Instant Meshes
    vertices, faces,          # as above
    target_faces=20000,       # about this many faces (pure quads: the count after the final subdivision)
    rosy=4, posy=4,           # 4/4 for quads; 6/3 for triangles
    crease_angle=-1.0,        # degrees; -1 disables
    extrinsic=False, align_to_boundaries=False,
    smooth_iterations=2,      # Instant Meshes' own smoothing and reprojection passes
    pure_quad=True,           # subdivide the quad-dominant result into quads (4x faces: the target is divided by 4 first)
    deterministic=True,
    orientation=None,         # (n, 3) float32: a direction per input vertex for the orientation field to follow
    orientation_weight=None,  # (n,) float32 in 0..1: 0 leaves the vertex free, 1 pins it
    freeze_orientation=False, edge_length=None,
    quiet=True)               # swallow the library's console chatter
out["vertices"], out["corners"], out["sizes"], out["singularities"], out["vertices_added"], out["seconds"]
```

QuadriFlow (Huang et al. 2018, BSD-3) is vendored from Blender's own copy in `third_party/quadriflow/`
(no Boost; see its README.sculptkit) and driven from `src/qflow_bridge.cpp`, a separate translation unit so
its headers never meet Instant Meshes'.

`orientation` is the hook for fRemesher's layout: a field computed elsewhere goes in as per-vertex tangent directions.
In `quadriflow` it freezes the field. In `remesh`, `orientation_weight` sets how firmly each vertex holds it; the
hierarchy keeps pinned vertices and smooths the rest, and vertices the library adds when it subdivides a coarse input
get no constraint.

## Known limits of Instant Meshes

- Extraction's "Step 5: snapping and removing unnecessary edges" is a repeat-until-no-change loop over all
  vertices that can run for many minutes on a noisy 1.3M-face input at a 100k target. Smoothing the input to
  the target scale first (what fRemesher already does) avoids it in the cases measured.
- Instant Meshes has no seed, no symmetry and no attribute transfer; fRemesher's own projection and relaxation
  run after the engine.
