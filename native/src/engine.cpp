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

namespace nb = nanobind;

int nprocs = -1;  // Instant Meshes' thread count: -1 lets TBB decide

using Points = nb::ndarray<const float, nb::shape<-1, 3>, nb::c_contig, nb::device::cpu>;
using Triangles = nb::ndarray<const int32_t, nb::shape<-1, 3>, nb::c_contig, nb::device::cpu>;
using Weights = nb::ndarray<const float, nb::shape<-1>, nb::c_contig, nb::device::cpu>;

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
    int singularities = 0;
    double seconds = 0.0;
};

static Result run(const float *vertices_in, size_t n_vertices, const int32_t *faces_in, size_t n_faces,
                  int face_count, int rosy, int posy, float crease_angle, bool extrinsic,
                  bool align_to_boundaries, int smooth_iter, bool pure_quad, bool deterministic,
                  const float *orientation, const float *orientation_weight, bool freeze_orientation) {
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
        size_t count = std::min<size_t>(n_vertices, mRes.V().cols());
        for (size_t i = 0; i < count; ++i) {
            Vector3f d(orientation[i * 3], orientation[i * 3 + 1], orientation[i * 3 + 2]);
            Vector3f n = mRes.N().col(i);
            d -= n * n.dot(d);
            if (d.squaredNorm() > 1e-12f)
                mRes.Q().col(i) = d.normalized();
        }
        mRes.propagateSolution(rosy);
        mRes.setFrozenQ(true);
    }
    bool constrained = !frozen && (orientation != nullptr || align_to_boundaries);
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
                    mRes.CQ().col(i0) = mRes.CQ().col(i1) = edge;
                    mRes.CQw()[i0] = mRes.CQw()[i1] = mRes.COw()[i0] = mRes.COw()[i1] = 1.0f;
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
                       bool freeze_orientation, bool quiet) {
    if (faces.shape(0) == 0)
        throw std::runtime_error("the mesh has no faces");
    if (orientation && orientation->shape(0) != vertices.shape(0))
        throw std::runtime_error("orientation needs one row per vertex");
    if (orientation_weight && orientation_weight->shape(0) != vertices.shape(0))
        throw std::runtime_error("orientation_weight needs one value per vertex");
    int face_count = pure_quad && posy == 4 ? std::max(1, target_faces / 4) : target_faces;
    Result out;
    {
        nb::gil_scoped_release release;
        Quiet hush(quiet);
        out = run(vertices.data(), vertices.shape(0), faces.data(), faces.shape(0), face_count, rosy, posy,
                  crease_angle, extrinsic, align_to_boundaries, smooth_iterations, pure_quad, deterministic,
                  orientation ? orientation->data() : nullptr,
                  orientation_weight ? orientation_weight->data() : nullptr, freeze_orientation);
    }
    nb::dict result;
    size_t n = out.vertices.size() / 3;
    result["vertices"] = make_array(std::move(out.vertices), n, 3);
    result["corners"] = make_array(std::move(out.corners), out.corners.size(), 0);
    result["sizes"] = make_array(std::move(out.sizes), out.sizes.size(), 0);
    result["singularities"] = out.singularities;
    result["seconds"] = out.seconds;
    return result;
}

NB_MODULE(_engine, m) {
    m.doc() = "Sculpt Kit's quad remesh engine: Instant Meshes in memory";
    m.def("version", [] { return "0.0.1 (Instant Meshes 7b31608)"; });
    m.def("remesh", &remesh, nb::arg("vertices"), nb::arg("faces"), nb::arg("target_faces") = -1,
          nb::arg("rosy") = 4, nb::arg("posy") = 4, nb::arg("crease_angle") = -1.0f, nb::arg("extrinsic") = false,
          nb::arg("align_to_boundaries") = false, nb::arg("smooth_iterations") = 2, nb::arg("pure_quad") = true,
          nb::arg("deterministic") = true, nb::arg("orientation").none() = nb::none(),
          nb::arg("orientation_weight").none() = nb::none(), nb::arg("freeze_orientation") = false,
          nb::arg("quiet") = true,
          "Remesh triangles (n x 3 float32 vertices, m x 3 int32 faces) into quads. Returns a dict with "
          "vertices, corners (flat), sizes (per face), singularities and seconds.");
}
