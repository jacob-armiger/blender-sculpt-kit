"""Sculpt Kit's native quad-remesh engine.

Instant Meshes (Jakob et al. 2015, BSD-3) run in memory on numpy arrays, with a hook to hand it our own
orientation field (the start of Option C in the remesher plan); and QuadriFlow (Huang et al. 2018, BSD-3,
Blender's copy), pure quads with an integer-consistent layout, which can take the same field frozen.

    import numpy as np, sculpt_kit_remesh
    out = sculpt_kit_remesh.remesh(vertices, triangles, target_faces=20000)
    out["vertices"]   # (n, 3) float32
    out["corners"]    # flat int32 vertex indices, face after face
    out["sizes"]      # int32 side count per face: 4 for quads; 3, 5, 6 appear in quad-dominant mode
    out["singularities"], out["seconds"]
"""
from ._engine import remesh, version

try:  # wheels built before QuadriFlow was added have only remesh()
    from ._engine import quadriflow
except ImportError:
    quadriflow = None

__all__ = ["remesh", "quadriflow", "version"]
