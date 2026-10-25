"""The kit's native remesh engine (native/, the sculpt_kit_remesh module), and the mesh helpers around it.

The engine carries Instant Meshes (Jakob et al. 2015, BSD-3) and QuadriFlow (Huang et al. 2018, BSD-3). It is
bound in memory: arrays in, polygons out, and a hook for an orientation field computed elsewhere (remesh_field.py).
Where its wheel is not built for the platform, sculpt_kit_remesh is None and fRemesher falls back to Blender's own
QuadriFlow (remesh.py).

remesh() here drives the engine's Instant Meshes entry. fRemesher uses it only with an engine built before
QuadriFlow was added to it; with the current engine remesh_field.py calls the engine's quadriflow() instead.
Pure quads come from subdividing every face of Instant Meshes' quad-dominant result into four, so it is asked for
a quarter of the target count; it has no seed and no symmetry.
"""
import time

import numpy as np

load_error = None  # why the engine did not load, for the console and bug reports
try:
    import sculpt_kit_remesh  # our own binding (native/): in memory, polygons out, orientation hook
except Exception as e:  # missing, or present but unloadable (a broken install, a missing system library)
    sculpt_kit_remesh = None
    load_error = "%s: %s" % (type(e).__name__, e)
    print("Sculpt Kit: fRemesher's engine could not be loaded, Blender's QuadriFlow stands in (%s)" % load_error)


def available():
    return sculpt_kit_remesh is not None


def _triangles(mesh):
    """The mesh as triangles, through its loop triangles (fast, and n-gons come along)."""
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    return tris.reshape(-1, 3)


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


def _fill_holes(mesh, keep=None):
    """Close the holes the remesher leaves where its grid did not meet (36 to 272 open edges on a sphere in
    the measurements), with quads where four sides allow it. keep(vertices) -> True leaves a hole open: a real
    opening of the sculpt, or the cut of a half to be mirrored. Returns how many edges were open."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    open_edges = [e for e in bm.edges if e.is_boundary]
    if keep is not None and open_edges:
        # the open edges' loops, by walking from edge to edge through shared vertices
        loops, seen = [], set()
        for start in open_edges:
            if start in seen:
                continue
            loop, stack = [], [start]
            seen.add(start)
            while stack:
                e = stack.pop()
                loop.append(e)
                for v in e.verts:
                    for f in v.link_edges:
                        if f.is_boundary and f not in seen:
                            seen.add(f)
                            stack.append(f)
            loops.append(loop)
        open_edges = [e for loop in loops if not keep({v for e in loop for v in e.verts}) for e in loop]
    count = len([e for e in bm.edges if e.is_boundary])
    if open_edges:
        bmesh.ops.holes_fill(bm, edges=open_edges, sides=4)
        still = [e for e in open_edges if e.is_valid and e.is_boundary]
        if still:
            bmesh.ops.holes_fill(bm, edges=still, sides=0)
        bm.to_mesh(mesh)
        mesh.update()
    bm.free()
    return count


def remesh(mesh, target_faces, pure_quad=True, crease_angle=-1.0, deterministic=True, smooth_iterations=2,
           fill_holes=True, orientation=None, orientation_weight=None, freeze_orientation=False,
           align_to_boundaries=False, edge_length=None):
    """Rebuild the mesh with Instant Meshes. Returns timings and counts; raises RuntimeError when it fails.
    fill_holes closes the gaps it leaves: pass False for a mesh that is meant to have open edges.
    orientation (one direction per vertex) and orientation_weight (0 to 1 each) steer the layout; they need
    the native engine."""
    if not available():
        raise RuntimeError("the native remesh engine is not built for this platform")
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.attributes["position"].data.foreach_get("vector", co)
    tris = _triangles(mesh)
    if not len(tris):
        raise RuntimeError("the mesh has no faces")
    stats = {"engine": "native"}
    started = time.perf_counter()
    steering = {}
    if orientation is not None:
        steering["orientation"] = np.ascontiguousarray(orientation, dtype=np.float32)
        if orientation_weight is not None:
            steering["orientation_weight"] = np.ascontiguousarray(orientation_weight, dtype=np.float32)
        if freeze_orientation:  # wheels built before the freeze hook do not know the argument
            steering["freeze_orientation"] = True
    if align_to_boundaries:  # quads end square-on at open edges (a half to be mirrored)
        steering["align_to_boundaries"] = True
    if edge_length is not None:  # a sizing field: the quad edge wanted at each vertex, in mesh units
        # Pure quads come from subdividing a quad-dominant result in four, so its edges are twice as long.
        steering["edge_length"] = np.ascontiguousarray(edge_length, dtype=np.float32) * (2.0 if pure_quad else 1.0)
    out = sculpt_kit_remesh.remesh(
        co.reshape(-1, 3), tris, target_faces=target_faces, pure_quad=pure_quad, crease_angle=crease_angle,
        deterministic=deterministic, smooth_iterations=smooth_iterations, **steering)
    stats["remesh"] = time.perf_counter() - started
    stats["singularities"] = out["singularities"]
    stats["vertices_added"] = out.get("vertices_added", 0)
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
