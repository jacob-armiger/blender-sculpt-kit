/*
    engine.cpp -- Sculpt Kit's remesh engine: Instant Meshes in memory.

    Follows Instant Meshes' batch.cpp step for step (BSD-3, Jakob et al. 2015), but takes and returns arrays
    instead of files, keeps polygons of any side count, and accepts per-vertex orientation constraints so a
    field computed elsewhere can drive the layout.
*/
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/optional.h>

#include <chrono>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <vector>

#include "common.h"
#include "dedge.h"
#include "subdivide.h"
#include "meshstats.h"
#include "hierarchy.h"
#include "field.h"
#include "normal.h"
#include "extract.h"
#include "bvh.h"

#include "qflow_bridge.hpp"

namespace nb = nanobind;

int nprocs = -1;  // Instant Meshes' thread count: -1 lets TBB decide

using Points = nb::ndarray<const float, nb::shape<-1, 3>, nb::c_contig, nb::device::cpu>;
using Triangles = nb::ndarray<const int32_t, nb::shape<-1, 3>, nb::c_contig, nb::device::cpu>;
using Weights = nb::ndarray<const float, nb::shape<-1>, nb::c_contig, nb::device::cpu>;
using Sizes = nb::ndarray<const float, nb::shape<-1, 2>, nb::c_contig, nb::device::cpu>;

/* The library narrates every step on std::cout. Swallowed unless asked for. */
struct Quiet {
    std::streambuf *saved = nullptr;
    std::ostringstream sink;
    explicit Quiet(bool on) { if (on) saved = std::cout.rdbuf(sink.rdbuf()); }
    ~Quiet() { if (saved) std::cout.rdbuf(saved); }
};

template <typename T> nb::ndarray<nb::numpy, T> make_array(std::vector<T> &&values, size_t rows, size_t cols) {
    T *data = new T[values.size()];
    std::copy(values.begin(), values.end(), data);
    nb::capsule owner(data, [](void *p) noexcept { delete[] (T *) p; });
    if (cols == 0)
        return nb::ndarray<nb::numpy, T>(data, {rows}, owner);
    return nb::ndarray<nb::numpy, T>(data, {rows, cols}, owner);
}

struct Result {
    std::vector<float> vertices;
    std::vector<int32_t> corners, sizes;
    int singularities = 0, vertices_added = 0;
    double seconds = 0.0;
};

static Result run(const float *vertices_in, size_t n_vertices, const int32_t *faces_in, size_t n_faces,
                  int face_count, int rosy, int posy, float crease_angle, bool extrinsic,
                  bool align_to_boundaries, int smooth_iter, bool pure_quad, bool deterministic,
                  const float *orientation, const float *orientation_weight, bool freeze_orientation,
                  const float *edge_length) {
    auto started = std::chrono::steady_clock::now();
    Result out;

    MatrixXu F(3, n_faces);
    MatrixXf V(3, n_vertices), N;
    VectorXf A;
    for (size_t i = 0; i < n_vertices; ++i)
        for (int j = 0; j < 3; ++j)
            V(j, i) = vertices_in[i * 3 + j];
    for (size_t i = 0; i < n_faces; ++i)
        for (int j = 0; j < 3; ++j)
            F(j, i) = (uint32_t) faces_in[i * 3 + j];

    MeshStats stats = compute_mesh_stats(F, V, deterministic);
    if (face_count <= 0)
        face_count = std::max<int>(1, (int) n_vertices / 16);
    Float face_area = stats.mSurfaceArea / face_count;
    Float scale = posy == 4 ? std::sqrt(face_area) : (2 * std::sqrt(face_area * std::sqrt(1.f / 3.f)));
    if (edge_length != nullptr) {  // a sizing field: the global scale becomes its mean, used where no vertex applies
        double total = 0.0;
        for (size_t i = 0; i < n_vertices; ++i)
            total += edge_length[i];
        scale = (Float) (total / std::max<size_t>(n_vertices, 1));
    }

    /* Subdivide the mesh if it is too coarse for the asked edge length (as batch.cpp does). New vertices
       go at the end, so the caller's per-vertex constraints still line up with the first n_vertices. */
    VectorXu V2E, E2E;
    VectorXb boundary, nonManifold;
    if (stats.mMaximumEdgeLength * 2 > scale || stats.mMaximumEdgeLength > stats.mAverageEdgeLength * 2) {
        build_dedge(F, V, V2E, E2E, boundary, nonManifold);
        subdivide(F, V, V2E, E2E, boundary, nonManifold,
                  std::min(scale / 2, (Float) stats.mAverageEdgeLength * 2), deterministic);
    }
    build_dedge(F, V, V2E, E2E, boundary, nonManifold);
    AdjacencyMatrix adj = generate_adjacency_matrix_uniform(F, V2E, E2E, nonManifold);
    std::set<uint32_t> crease_in, crease_out;
    if (crease_angle >= 0)
        generate_crease_normals(F, V, V2E, E2E, boundary, nonManifold, crease_angle, N, crease_in);
    else
        generate_smooth_normals(F, V, V2E, E2E, nonManifold, N);
    compute_dual_vertex_areas(F, V, V2E, E2E, nonManifold, A);

    MultiResolutionHierarchy mRes;
    if (edge_length != nullptr) {
        /* The sizing field, one value per vertex; vertices the subdivision added take a neighbour's. */
        size_t total = V.cols();
        VectorXf S(total);
        std::vector<bool> known(total, false);
        for (size_t i = 0; i < std::min<size_t>(n_vertices, total); ++i) {
            S[i] = std::max(edge_length[i], 1e-6f);
            known[i] = true;
        }
        for (int pass = 0; pass < 32; ++pass) {
            size_t filled = 0;
            for (size_t i = 0; i < total; ++i) {
                if (known[i])
                    continue;
                for (const Link *link = adj[i]; link != adj[i + 1]; ++link)
                    if (known[link->id]) {
                        S[i] = S[link->id];
                        known[i] = true;
                        ++filled;
                        break;
                    }
            }
            if (!filled)
                break;
        }
        for (size_t i = 0; i < total; ++i)
            if (!known[i])
                S[i] = scale;
        mRes.setS(std::move(S));
    }
    mRes.setE2E(std::move(E2E));
    mRes.setAdj(std::move(adj));
    mRes.setF(std::move(F));
    mRes.setV(std::move(V));
    mRes.setA(std::move(A));
    mRes.setN(std::move(N));
    mRes.setScale(scale);
    mRes.build(deterministic);
    mRes.resetSolution();

    /* Two ways to hand over a field. Frozen: it is written into Q at the finest level, restricted to the
       coarser ones and never optimised, so the position solve and extraction see exactly the given field
       (vertices without a direction keep a random one, so give every vertex one). Soft: it goes in as the
       library's own constraints, which its smoother blends with at every level. */
    bool frozen = orientation != nullptr && freeze_orientation;
    if (frozen) {
        size_t total = mRes.V().cols(), count = std::min<size_t>(n_vertices, total);
        std::vector<bool> known(total, false);
        for (size_t i = 0; i < count; ++i) {
            Vector3f d(orientation[i * 3], orientation[i * 3 + 1], orientation[i * 3 + 2]);
            Vector3f n = mRes.N().col(i);
            d -= n * n.dot(d);
            if (d.squaredNorm() > 1e-12f) {
                mRes.Q().col(i) = d.normalized();
                known[i] = true;
            }
        }
        /* Vertices the library added when subdividing a coarse input (they come after the caller's) have
           no direction of their own: they take a known neighbour's, projected into their tangent plane,
           pass by pass until every vertex has one. Left random they would riddle the field with poles. */
        const AdjacencyMatrix &adj = mRes.adj(0);
        for (int pass = 0; pass < 32; ++pass) {
            size_t filled = 0;
            for (size_t i = 0; i < total; ++i) {
                if (known[i])
                    continue;
                for (const Link *link = adj[i]; link != adj[i + 1]; ++link) {
                    if (!known[link->id])
                        continue;
                    Vector3f n = mRes.N().col(i), d = mRes.Q().col(link->id);
                    d -= n * n.dot(d);
                    if (d.squaredNorm() > 1e-12f) {
                        mRes.Q().col(i) = d.normalized();
                        known[i] = true;
                        ++filled;
                        break;
                    }
                }
            }
            if (!filled)
                break;
        }
        mRes.propagateSolution(rosy);
        mRes.setFrozenQ(true);
    }
    /* Boundary alignment still applies with a frozen field: its position constraints pin the lattice to the
       boundary so quads end on it square-on (what a mirrored half needs at the seam); only its orientation
       part is left out, since the frozen field already says which way to go there. */
    bool constrained = (!frozen && orientation != nullptr) || align_to_boundaries;
    if (constrained)
        mRes.clearConstraints();
    if (orientation != nullptr && !frozen) {
        size_t count = std::min<size_t>(n_vertices, mRes.V().cols());
        for (size_t i = 0; i < count; ++i) {
            float w = orientation_weight ? orientation_weight[i] : 1.0f;
            if (w <= 0.0f)
                continue;
            Vector3f d(orientation[i * 3], orientation[i * 3 + 1], orientation[i * 3 + 2]);
            Vector3f n = mRes.N().col(i);
            d -= n * n.dot(d);  // the field lives in the tangent plane
            if (d.squaredNorm() < 1e-12f)
                continue;
            mRes.CQ().col(i) = d.normalized();
            mRes.CQw()[i] = std::min(w, 1.0f);
        }
    }
    if (align_to_boundaries) {
        for (uint32_t i = 0; i < 3 * mRes.F().cols(); ++i) {
            if (mRes.E2E()[i] == INVALID) {
                uint32_t i0 = mRes.F()(i % 3, i / 3), i1 = mRes.F()((i + 1) % 3, i / 3);
                Vector3f p0 = mRes.V().col(i0), p1 = mRes.V().col(i1);
                Vector3f edge = p1 - p0;
                if (edge.squaredNorm() > 0) {
                    edge.normalize();
                    mRes.CO().col(i0) = p0;
                    mRes.CO().col(i1) = p1;
                    mRes.COw()[i0] = mRes.COw()[i1] = 1.0f;
                    if (!frozen) {
                        mRes.CQ().col(i0) = mRes.CQ().col(i1) = edge;
                        mRes.CQw()[i0] = mRes.CQw()[i1] = 1.0f;
                    }
                }
            }
        }
    }
    if (constrained)
        mRes.propagateConstraints(rosy, posy);

    BVH *bvh = nullptr;
    if (smooth_iter > 0) {
        bvh = new BVH(&mRes.F(), &mRes.V(), &mRes.N(), stats.mAABB);
        bvh->build();
    }

    Optimizer optimizer(mRes, false);
    optimizer.setRoSy(rosy);
    optimizer.setPoSy(posy);
    optimizer.setExtrinsic(extrinsic);
    if (!frozen) {  // optimizeOrientations(-1) would also unfreeze Q
        optimizer.optimizeOrientations(-1);
        optimizer.notify();
        optimizer.wait();
    }
    std::map<uint32_t, uint32_t> sing;
    compute_orientation_singularities(mRes, sing, extrinsic, rosy);
    out.singularities = (int) sing.size();
    out.vertices_added = (int) mRes.V().cols() - (int) n_vertices;  // from the library's own subdivision
    optimizer.optimizePositions(-1);
    optimizer.notify();
    optimizer.wait();
    optimizer.shutdown();

    MatrixXf O_extr, N_extr, Nf_extr;
    std::vector<std::vector<TaggedLink>> adj_extr;
    extract_graph(mRes, extrinsic, rosy, posy, adj_extr, O_extr, N_extr, crease_in, crease_out, deterministic);
    MatrixXu F_extr;
    extract_faces(adj_extr, O_extr, N_extr, Nf_extr, F_extr, posy, mRes.scale(), crease_out, true, pure_quad,
                  bvh, smooth_iter);
    if (bvh)
        delete bvh;

    out.vertices.resize(O_extr.cols() * 3);
    for (uint32_t i = 0; i < O_extr.cols(); ++i)
        for (int j = 0; j < 3; ++j)
            out.vertices[i * 3 + j] = O_extr(j, i);

    /* Faces come as a fixed-width matrix. In quad-dominant mode a polygon with 3, 5 or more sides is stored
       as one row per corner with F(2) == F(3) == a shared key, each row holding one directed edge of the
       polygon (the same encoding meshio.cpp's write_obj decodes). */
    std::map<uint32_t, std::map<uint32_t, uint32_t>> irregular;
    for (uint32_t f = 0; f < F_extr.cols(); ++f) {
        if (F_extr.rows() == 4 && F_extr(2, f) == F_extr(3, f)) {
            irregular[F_extr(2, f)][F_extr(0, f)] = F_extr(1, f);
            continue;
        }
        for (int j = 0; j < F_extr.rows(); ++j)
            out.corners.push_back((int32_t) F_extr(j, f));
        out.sizes.push_back((int32_t) F_extr.rows());
    }
    for (auto &item : irregular) {
        auto &ring = item.second;
        uint32_t v = ring.begin()->first, first = v, i = 0;
        int32_t count = 0;
        while (true) {
            out.corners.push_back((int32_t) v);
            ++count;
            v = ring[v];
            if (v == first || ++i == ring.size())
                break;
        }
        out.sizes.push_back(count);
    }
    out.seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    return out;
}

static nb::dict remesh(Points vertices, Triangles faces, int target_faces, int rosy, int posy, float crease_angle,
                       bool extrinsic, bool align_to_boundaries, int smooth_iterations, bool pure_quad,
                       bool deterministic, std::optional<Points> orientation, std::optional<Weights> orientation_weight,
                       bool freeze_orientation, std::optional<Weights> edge_length, bool quiet) {
    if (faces.shape(0) == 0)
        throw std::runtime_error("the mesh has no faces");
    if (orientation && orientation->shape(0) != vertices.shape(0))
        throw std::runtime_error("orientation needs one row per vertex");
    if (orientation_weight && orientation_weight->shape(0) != vertices.shape(0))
        throw std::runtime_error("orientation_weight needs one value per vertex");
    if (edge_length && edge_length->shape(0) != vertices.shape(0))
        throw std::runtime_error("edge_length needs one value per vertex");
    int face_count = pure_quad && posy == 4 ? std::max(1, target_faces / 4) : target_faces;
    Result out;
    {
        nb::gil_scoped_release release;
        Quiet hush(quiet);
        out = run(vertices.data(), vertices.shape(0), faces.data(), faces.shape(0), face_count, rosy, posy,
                  crease_angle, extrinsic, align_to_boundaries, smooth_iterations, pure_quad, deterministic,
                  orientation ? orientation->data() : nullptr,
                  orientation_weight ? orientation_weight->data() : nullptr, freeze_orientation,
                  edge_length ? edge_length->data() : nullptr);
    }
    nb::dict result;
    size_t n = out.vertices.size() / 3;
    result["vertices"] = make_array(std::move(out.vertices), n, 3);
    result["corners"] = make_array(std::move(out.corners), out.corners.size(), 0);
    result["sizes"] = make_array(std::move(out.sizes), out.sizes.size(), 0);
    result["singularities"] = out.singularities;
    result["vertices_added"] = out.vertices_added;
    result["seconds"] = out.seconds;
    return result;
}

static nb::dict quadriflow(Points vertices, Triangles faces, int target_faces, std::optional<Points> orientation,
                           bool preserve_sharp, bool preserve_boundary, bool adaptive_scale, bool minimum_cost_flow,
                           int seed, bool fill_holes, std::optional<Sizes> scale) {
    if (faces.shape(0) == 0)
        throw std::runtime_error("the mesh has no faces");
    if (orientation && orientation->shape(0) != vertices.shape(0))
        throw std::runtime_error("orientation needs one row per vertex");
    if (scale && scale->shape(0) != vertices.shape(0))
        throw std::runtime_error("scale needs one row per vertex");
    if (scale && !orientation)
        throw std::runtime_error("scale needs an orientation: its two sizes are along and across it");
    QFlowOptions options;
    options.target_faces = target_faces;
    options.preserve_sharp = preserve_sharp;
    options.preserve_boundary = preserve_boundary;
    options.adaptive_scale = adaptive_scale;
    options.minimum_cost_flow = minimum_cost_flow;
    options.seed = seed;
    options.fill_holes = fill_holes;
    QFlowResult out;
    {
        nb::gil_scoped_release release;
        out = quadriflow_run(vertices.data(), vertices.shape(0), faces.data(), faces.shape(0),
                             orientation ? orientation->data() : nullptr, options, scale ? scale->data() : nullptr);
    }
    if (!out.ok)
        throw std::runtime_error(out.error);
    nb::dict result;
    size_t n = out.vertices.size() / 3, m = out.quads.size() / 4;
    result["vertices"] = make_array(std::move(out.vertices), n, 3);
    result["quads"] = make_array(std::move(out.quads), m, 4);
    result["singularities"] = out.singularities;
    result["position_singularities"] = out.position_singularities;
    result["vertices_added"] = out.vertices_added;
    result["seconds"] = out.seconds;
    return result;
}

NB_MODULE(_engine, m) {
    m.doc() = "Sculpt Kit's quad remesh engines: Instant Meshes and QuadriFlow in memory";
    m.def("version", [] { return "0.0.3 (Instant Meshes 7b31608, QuadriFlow from Blender)"; });
    m.def("quadriflow", &quadriflow, nb::arg("vertices"), nb::arg("faces"), nb::arg("target_faces") = 10000,
          nb::arg("orientation").none() = nb::none(), nb::arg("preserve_sharp") = false,
          nb::arg("preserve_boundary") = false, nb::arg("adaptive_scale") = false,
          nb::arg("minimum_cost_flow") = false, nb::arg("seed") = 0, nb::arg("fill_holes") = true,
          nb::arg("scale").none() = nb::none(),
          "Remesh triangles into pure quads with QuadriFlow. orientation (one direction per vertex) freezes the "
          "orientation field to it; scale (n x 2: along each orientation direction and across it, relative to the uniform quad size) sets the quad sizes. Returns a dict with vertices (n x 3), quads (m x 4), singularities, "
          "position_singularities and seconds; raises RuntimeError when it fails.");
    m.def("remesh", &remesh, nb::arg("vertices"), nb::arg("faces"), nb::arg("target_faces") = -1,
          nb::arg("rosy") = 4, nb::arg("posy") = 4, nb::arg("crease_angle") = -1.0f, nb::arg("extrinsic") = false,
          nb::arg("align_to_boundaries") = false, nb::arg("smooth_iterations") = 2, nb::arg("pure_quad") = true,
          nb::arg("deterministic") = true, nb::arg("orientation").none() = nb::none(),
          nb::arg("orientation_weight").none() = nb::none(), nb::arg("freeze_orientation") = false,
          nb::arg("edge_length").none() = nb::none(), nb::arg("quiet") = true,
          "Remesh triangles (n x 3 float32 vertices, m x 3 int32 faces) into quads. Returns a dict with "
          "vertices, corners (flat), sizes (per face), singularities and seconds.");
}
