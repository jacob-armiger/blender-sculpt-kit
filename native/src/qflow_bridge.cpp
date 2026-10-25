/*
    qflow_bridge.cpp -- QuadriFlow in memory, with a frozen orientation field.

    Follows Blender's intern/quadriflow/quadriflow_capi.cpp step for step (the same vendored sources, see
    third_party/quadriflow/README.sculptkit), except that the orientation optimisation is replaced by our
    field when one is given.
*/
#include "qflow_bridge.hpp"

#include <algorithm>
#include <chrono>
#include <exception>

#include "field-math.hpp"
#include "optimizer.hpp"
#include "parametrizer.hpp"

using namespace qflow;

/* Our field into Q at the finest level; vertices QuadriFlow added when subdividing take a known neighbour's
   direction pass by pass; then the field is carried up to the coarser levels the way the end of
   Optimizer::optimize_orientations does, so optimize_positions sees it at every level. */
static void freeze_orientation(Hierarchy &mRes, const float *orientation, size_t n_vertices) {
    MatrixXd &Q = mRes.mQ[0];
    const MatrixXd &N = mRes.mN[0];
    size_t total = Q.cols(), count = std::min(n_vertices, total);
    std::vector<bool> known(total, false);
    for (size_t i = 0; i < count; ++i) {
        Vector3d d(orientation[i * 3], orientation[i * 3 + 1], orientation[i * 3 + 2]);
        Vector3d n = N.col(i);
        d -= n * n.dot(d);
        if (d.squaredNorm() > 1e-24) {
            Q.col(i) = d.normalized();
            known[i] = true;
        }
    }
    /* Where QuadriFlow has its own orientation constraint (open edges, with preserve_boundary) it wins, as it
       does in QuadriFlow's own solve at full weight: its integer step treats those edges as fixed lines and
       needs the field exactly along them, not nearly. */
    const MatrixXd &CQ = mRes.mCQ[0];
    const VectorXd &CQw = mRes.mCQw[0];
    for (size_t i = 0; i < total && (size_t) CQw.size() == total; ++i) {
        if (CQw[i] == 0)
            continue;
        Vector3d n = N.col(i), d = CQ.col(i);
        d -= n * n.dot(d);
        if (d.squaredNorm() > 1e-24) {
            Q.col(i) = d.normalized();
            known[i] = true;
        }
    }
    const AdjacentMatrix &adj = mRes.mAdj[0];
    for (int pass = 0; pass < 64; ++pass) {
        size_t filled = 0;
        for (size_t i = 0; i < total; ++i) {
            if (known[i])
                continue;
            for (const Link &link : adj[i]) {
                if (!known[link.id])
                    continue;
                Vector3d n = N.col(i), d = Q.col(link.id);
                d -= n * n.dot(d);
                if (d.squaredNorm() > 1e-24) {
                    Q.col(i) = d.normalized();
                    known[i] = true;
                    ++filled;
                    break;
                }
            }
        }
        if (!filled)
            break;
    }
    for (size_t l = 0; l + 1 < mRes.mN.size(); ++l) {
        const MatrixXd &N_l = mRes.mN[l], &N_next = mRes.mN[l + 1], &Q_l = mRes.mQ[l];
        MatrixXd &Q_next = mRes.mQ[l + 1];
        const MatrixXi &toUpper = mRes.mToUpper[l];
        for (int i = 0; i < toUpper.cols(); ++i) {
            Vector2i upper = toUpper.col(i);
            Vector3d q;
            if (upper[1] != -1) {
                auto value = compat_orientation_extrinsic_4(Q_l.col(upper[0]), N_l.col(upper[0]),
                                                            Q_l.col(upper[1]), N_l.col(upper[1]));
                q = value.first + value.second;
            } else {
                q = Q_l.col(upper[0]);
            }
            Vector3d n = N_next.col(i);
            q -= n.dot(q) * n;
            if (q.squaredNorm() > RCPOVERFLOW)
                q.normalize();
            Q_next.col(i) = q;
        }
    }
}

/* Vertex b's scale as seen in vertex a's frame: S holds a size along each vertex's own Q and one along
   n x Q, so where b's Q is a quarter turn (or three) from a's the two are swapped. */
static Vector2d matched_scale(const MatrixXd &Q, const MatrixXd &N, const MatrixXd &S, int a, int b) {
    auto index = compat_orientation_extrinsic_index_4(Q.col(a), N.col(a), Q.col(b), N.col(b));
    Vector2d s = S.col(b);
    if ((index.second + 4 - index.first) % 2 == 1)
        std::swap(s[0], s[1]);
    return s;
}

/* Our quad sizes into QuadriFlow's scale field S at the finest level: two per vertex, along the direction
   given for it (orientation) and across it, relative to the uniform quad size. Where QuadriFlow's Q is a
   quarter turn from that direction (open edges keep their own) the two are swapped. Vertices QuadriFlow
   added take a known neighbour's, then the field is carried to the coarser levels as
   Optimizer::optimize_scale ends, matched to each level's own Q. */
static void set_scale(Hierarchy &mRes, const float *scale, const float *orientation, size_t n_vertices) {
    MatrixXd &S = mRes.mS[0];
    const MatrixXd &Q = mRes.mQ[0], &N = mRes.mN[0];
    size_t total = S.cols(), count = std::min(n_vertices, total);
    std::vector<bool> known(total, false);
    for (size_t i = 0; i < count; ++i) {
        Vector2d s(std::max((double) scale[i * 2], 1e-3), std::max((double) scale[i * 2 + 1], 1e-3));
        Vector3d n = N.col(i), d(orientation[i * 3], orientation[i * 3 + 1], orientation[i * 3 + 2]);
        d -= n * n.dot(d);
        if (d.squaredNorm() > 1e-24) {
            auto index = compat_orientation_extrinsic_index_4(Q.col(i), n, d.normalized(), n);
            if ((index.second + 4 - index.first) % 2 == 1)
                std::swap(s[0], s[1]);
        }
        S.col(i) = s;
        known[i] = true;
    }
    const AdjacentMatrix &adj = mRes.mAdj[0];
    for (int pass = 0; pass < 64; ++pass) {
        size_t filled = 0;
        for (size_t i = 0; i < total; ++i) {
            if (known[i])
                continue;
            for (const Link &link : adj[i])
                if (known[link.id]) {
                    S.col(i) = matched_scale(Q, N, S, (int) i, link.id);
                    known[i] = true;
                    ++filled;
                    break;
                }
        }
        if (!filled)
            break;
    }
    for (size_t l = 0; l + 1 < mRes.mS.size(); ++l) {
        const MatrixXd &S_l = mRes.mS[l], &Q_l = mRes.mQ[l], &N_l = mRes.mN[l];
        const MatrixXd &Q_next = mRes.mQ[l + 1], &N_next = mRes.mN[l + 1];
        MatrixXd &S_next = mRes.mS[l + 1];
        const MatrixXi &toUpper = mRes.mToUpper[l];
        for (int i = 0; i < toUpper.cols(); ++i) {
            Vector2d s = Vector2d::Zero();
            int parts = 0;
            for (int k = 0; k < 2; ++k) {
                int child = toUpper(k, i);
                if (child == -1)
                    continue;
                Vector2d c = S_l.col(child);
                auto index = compat_orientation_extrinsic_index_4(Q_next.col(i), N_next.col(i), Q_l.col(child),
                                                                  N_l.col(child));
                if ((index.second + 4 - index.first) % 2 == 1)
                    std::swap(c[0], c[1]);
                s += c;
                ++parts;
            }
            S_next.col(i) = s / std::max(parts, 1);
        }
    }
}

QFlowResult quadriflow_run(const float *vertices, size_t n_vertices, const int32_t *triangles, size_t n_triangles,
                           const float *orientation, const QFlowOptions &options, const float *scale) {
    auto started = std::chrono::steady_clock::now();
    QFlowResult out;
    try {
        Parametrizer field;
        field.flag_preserve_sharp = options.preserve_sharp;
        field.flag_preserve_boundary = options.preserve_boundary;
        field.flag_adaptive_scale = options.adaptive_scale;
        field.flag_minimum_cost_flow = options.minimum_cost_flow;
        field.flag_fill_holes = options.fill_holes;
        if (options.seed)
            field.hierarchy.rng_seed = options.seed;

        field.V.resize(3, n_vertices);
        for (size_t i = 0; i < n_vertices; ++i)
            for (int j = 0; j < 3; ++j)
                field.V(j, i) = vertices[i * 3 + j];
        field.F.resize(3, n_triangles);
        for (size_t i = 0; i < n_triangles; ++i)
            for (int j = 0; j < 3; ++j)
                field.F(j, i) = triangles[i * 3 + j];

        field.NormalizeMesh();  // a uniform scale and offset: directions are unaffected
        field.Initialize(options.target_faces);
        out.vertices_added = (int) field.hierarchy.mV[0].cols() - (int) n_vertices;

        Hierarchy &mRes = field.hierarchy;
        if (field.flag_preserve_boundary) {
            mRes.clearConstraints();
            for (uint32_t i = 0; i < 3 * mRes.mF.cols(); ++i) {
                if (mRes.mE2E[i] == -1) {
                    uint32_t i0 = mRes.mF(i % 3, i / 3), i1 = mRes.mF((i + 1) % 3, i / 3);
                    Vector3d p0 = mRes.mV[0].col(i0), p1 = mRes.mV[0].col(i1);
                    Vector3d edge = p1 - p0;
                    if (edge.squaredNorm() > 0) {
                        edge.normalize();
                        mRes.mCO[0].col(i0) = p0;
                        mRes.mCO[0].col(i1) = p1;
                        mRes.mCQ[0].col(i0) = mRes.mCQ[0].col(i1) = edge;
                        mRes.mCQw[0][i0] = mRes.mCQw[0][i1] = mRes.mCOw[0][i0] = mRes.mCOw[0][i1] = 1.0;
                    }
                }
            }
            mRes.propagateConstraints();
        }

        if (orientation != nullptr)
            freeze_orientation(mRes, orientation, n_vertices);
        else
            Optimizer::optimize_orientations(mRes);
        field.ComputeOrientationSingularities();
        out.singularities = (int) field.singularities.size();

        if (field.flag_adaptive_scale == 1)
            field.EstimateSlope();
        Optimizer::optimize_scale(mRes, field.rho, field.flag_adaptive_scale);
        if (scale != nullptr && orientation != nullptr)
            set_scale(mRes, scale, orientation, n_vertices);
        field.flag_adaptive_scale = 1;
        Optimizer::optimize_positions(mRes, field.flag_adaptive_scale);
        field.ComputePositionSingularities();
        out.position_singularities = (int) field.pos_sing.size();

        if (!field.ComputeIndexMap(scale != nullptr ? 1 : 0)) {  // its layout reads S only when asked
            out.error = "QuadriFlow could not build the quad layout";
            return out;
        }
        out.vertices.resize(field.O_compact.size() * 3);
        for (size_t i = 0; i < field.O_compact.size(); ++i) {
            Vector3d p = field.O_compact[i] * field.normalize_scale + field.normalize_offset;
            for (int j = 0; j < 3; ++j)
                out.vertices[i * 3 + j] = (float) p[j];
        }
        out.quads.resize(field.F_compact.size() * 4);
        for (size_t i = 0; i < field.F_compact.size(); ++i)
            for (int j = 0; j < 4; ++j)
                out.quads[i * 4 + j] = field.F_compact[i][j];
        out.ok = true;
    } catch (const std::exception &e) {
        out.error = std::string("QuadriFlow failed: ") + e.what();
    }
    out.seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    return out;
}
