/*
    qflow_bridge.hpp -- QuadriFlow behind a plain interface, so its headers (Eigen with `using namespace`,
    its own Optimizer and Hierarchy) never meet Instant Meshes' in one translation unit.
*/
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

struct QFlowOptions {
    int target_faces = 10000;
    bool preserve_sharp = false;
    bool preserve_boundary = false;  // quads end on open edges; also keeps them as sharp lines
    bool adaptive_scale = false;
    bool minimum_cost_flow = false;  // QuadriFlow's slower, more thorough integer solve
    bool fill_holes = true;          // fill holes of under 25 vertices (its own gaps, and small real openings)
    int seed = 0;
};

struct QFlowResult {
    std::vector<float> vertices;  // n x 3
    std::vector<int32_t> quads;   // m x 4
    int singularities = 0;            // orientation singularities on the input, as QuadriFlow counts them
    int position_singularities = 0;   // where the position field does not close, before the integer solve
    int vertices_added = 0;           // by QuadriFlow's own subdivision of a coarse input
    double seconds = 0.0;
    bool ok = false;
    std::string error;
};

/* Remesh triangles into pure quads with QuadriFlow (Huang et al. 2018). When `orientation` is given (one
   direction per input vertex) the orientation field is frozen to it: written in at the finest level,
   restricted to the coarser ones, and never optimised, so the position solve, the integer flow and the
   extraction all work on exactly that field. When `scale` is given (two per input vertex, n x 2: the quad's
   length along its `orientation` direction and across it, relative to the uniform quad size, 2 = twice as
   long) it replaces QuadriFlow's own scale field the same way; it needs `orientation`. */
QFlowResult quadriflow_run(const float *vertices, size_t n_vertices, const int32_t *triangles, size_t n_triangles,
                           const float *orientation, const QFlowOptions &options, const float *scale = nullptr);
