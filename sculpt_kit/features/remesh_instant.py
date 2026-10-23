"""Instant Meshes as a second engine for Repairing Remesh (the Option B experiment).

Instant Meshes (Jakob et al. 2015, BSD-3) is reached two ways, both bundled in ../wheels and listed in the
manifest. First choice is the kit's own binding, sculpt_kit_remesh (built from native/): arrays in, polygons
out, and an orientation hook for a field computed elsewhere. Where that wheel is not built yet the PyPI
pyinstantmeshes wheel is used. Its remesh() function is not: it writes the mesh to a temporary OBJ, runs the
remesher, and reads the result back through a loader that splits every quad into two triangles, so the quads
never reach Python. remesh_file() leaves the output OBJ for us to parse, which keeps the quads, and its own
read-back of that file (which raises on faces with more than four sides) is caught and ignored.

Two things the engine does differently from QuadriFlow:
  - pure quads come from subdividing every face of its quad-dominant result into four, so it is asked for a
    quarter of the target count;
  - it has no seed and no symmetry. A new attempt is a different smoothing, not a different seed.
"""
import os
import tempfile
import time

import numpy as np

try:
    import sculpt_kit_remesh  # our own binding (native/): in memory, polygons out, orientation hook
except ImportError:
    sculpt_kit_remesh = None
try:
    import pyinstantmeshes  # the PyPI wheel: the fallback where our own is not built yet
except ImportError:
    pyinstantmeshes = None


def available():
    return sculpt_kit_remesh is not None or pyinstantmeshes is not None


def engine_name():
    return "native" if sculpt_kit_remesh is not None else "pyinstantmeshes" if pyinstantmeshes is not None else None


def _triangles(mesh):
    """The mesh as triangles, through its loop triangles (fast, and n-gons come along)."""
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    return tris.reshape(-1, 3)


def _write_obj(path, co, faces):
    with open(path, "w") as f:
        np.savetxt(f, co, fmt="v %.7g %.7g %.7g")
        np.savetxt(f, faces + 1, fmt="f " + " ".join(["%d"] * faces.shape[1]))


def _read_obj(path):
    """Vertices and faces grouped by side count, with unused vertices dropped."""
    verts, faces = [], {}
    with open(path) as f:
        for line in f:
            if line.startswith("v "):
                verts.append(line.split()[1:4])
            elif line.startswith("f "):
                idx = [int(p.split("/")[0]) - 1 for p in line.split()[1:]]
                faces.setdefault(len(idx), []).append(idx)
    co = np.array(verts, dtype=np.float32).reshape(-1, 3)
    faces = {n: np.array(fs, dtype=np.int32) for n, fs in faces.items()}
    used = np.zeros(len(co), dtype=bool)
    for fs in faces.values():
        used[fs.ravel()] = True
    renumber = np.cumsum(used) - 1
    return co[used], {n: renumber[fs] for n, fs in faces.items()}


def _replace_geometry(mesh, co, faces):
    """Give the mesh these vertices and faces (a dict of side count -> index array), keeping nothing else."""
    sizes = sorted(faces)
    loop_total = np.concatenate([np.full(len(faces[n]), n, dtype=np.int32) for n in sizes])
    corners = np.concatenate([faces[n].ravel() for n in sizes])
    mesh.clear_geometry()
    mesh.vertices.add(len(co))
    mesh.vertices.foreach_set("co", np.ascontiguousarray(co, dtype=np.float32).ravel())
    mesh.loops.add(len(corners))
    mesh.loops.foreach_set("vertex_index", corners)
    mesh.polygons.add(len(loop_total))
    mesh.polygons.foreach_set("loop_start", np.concatenate([[0], np.cumsum(loop_total)[:-1]]).astype(np.int32))
    mesh.polygons.foreach_set("loop_total", loop_total)
    mesh.update(calc_edges=True)
    mesh.validate()


def _fill_holes(mesh):
    """Close the small gaps Instant Meshes leaves where its grid did not meet (36 to 272 open edges on a
    sphere in the measurements), with quads where four sides allow it. Returns how many edges were open."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    open_edges = [e for e in bm.edges if e.is_boundary]
    if open_edges:
        bmesh.ops.holes_fill(bm, edges=open_edges, sides=4)
        still = [e for e in bm.edges if e.is_boundary]
        if still:
            bmesh.ops.holes_fill(bm, edges=still, sides=0)
        bm.to_mesh(mesh)
        mesh.update()
    bm.free()
    return len(open_edges)


def remesh(mesh, target_faces, pure_quad=True, crease_angle=-1.0, deterministic=True, smooth_iterations=2,
           fill_holes=True, orientation=None, orientation_weight=None, freeze_orientation=False):
    """Rebuild the mesh with Instant Meshes. Returns timings and counts; raises RuntimeError when it fails.
    fill_holes closes the gaps it leaves: pass False for a mesh that is meant to have open edges.
    orientation (one direction per vertex) and orientation_weight (0 to 1 each) steer the layout; they need
    our own engine."""
    if not available():
        raise RuntimeError("Instant Meshes is not installed for this platform")
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.attributes["position"].data.foreach_get("vector", co)
    tris = _triangles(mesh)
    if not len(tris):
        raise RuntimeError("the mesh has no faces")
    stats = {"engine": engine_name()}
    started = time.perf_counter()
    if sculpt_kit_remesh is not None:
        out = sculpt_kit_remesh.remesh(
            co.reshape(-1, 3), tris, target_faces=target_faces, pure_quad=pure_quad, crease_angle=crease_angle,
            deterministic=deterministic, smooth_iterations=smooth_iterations,
            orientation=None if orientation is None else np.ascontiguousarray(orientation, dtype=np.float32),
            orientation_weight=None if orientation_weight is None else np.ascontiguousarray(orientation_weight, dtype=np.float32),
            freeze_orientation=freeze_orientation)
        stats["remesh"] = time.perf_counter() - started
        stats["singularities"] = out["singularities"]
        started = time.perf_counter()
        sizes, corners = out["sizes"], out["corners"]
        starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
        faces = {int(n): corners[starts[sizes == n][:, None] + np.arange(n)] for n in np.unique(sizes)}
        if not faces:
            raise RuntimeError("Instant Meshes gave no faces")
        _replace_geometry(mesh, out["vertices"], faces)
        if fill_holes:
            stats["open edges filled"] = _fill_holes(mesh)
        stats["rebuild"] = time.perf_counter() - started
        stats["sides"] = {n: len(fs) for n, fs in sorted(faces.items())}
        return stats
    if orientation is not None:
        raise RuntimeError("steering the layout needs the kit's own engine, which is not built for this platform")
    with tempfile.TemporaryDirectory(prefix="sculptkit_im_") as folder:
        src, dst = os.path.join(folder, "in.obj"), os.path.join(folder, "out.obj")
        _write_obj(src, co.reshape(-1, 3), tris)
        stats["write"] = time.perf_counter() - started
        started = time.perf_counter()
        try:
            pyinstantmeshes.remesh_file(
                src, dst, target_face_count=max(1, target_faces // 4) if pure_quad else target_faces,
                pure_quad=pure_quad, crease_angle=crease_angle, deterministic=deterministic,
                smooth_iterations=smooth_iterations)
        except RuntimeError as error:
            if not os.path.exists(dst):  # the remesh itself failed, not the read-back
                raise RuntimeError("Instant Meshes failed: %s" % error) from None
        stats["remesh"] = time.perf_counter() - started
        started = time.perf_counter()
        out_co, faces = _read_obj(dst)
        if not faces:
            raise RuntimeError("Instant Meshes gave no faces")
    _replace_geometry(mesh, out_co, faces)
    if fill_holes:
        stats["open edges filled"] = _fill_holes(mesh)
    stats["rebuild"] = time.perf_counter() - started
    stats["sides"] = {n: len(fs) for n, fs in sorted(faces.items())}
    return stats
