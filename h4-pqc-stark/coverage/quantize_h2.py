"""Quantize the trained H2 GCN (GCNConv 165->64 ReLU, GCNConv 64->2) for one target node.

PyG GCNConv semantics (add_self_loops, symmetric norm over in-degree+1, messages source->target):
    out_t = sum_{s in in(t) U {t}} d(s)^-1/2 d(t)^-1/2 (x_s W) + b,   d(i) = indeg(i) + 1
Integer semantics proven by the circuit, for target v with layer-1 nodes U = in(v) U {v}:
    a1[u]  = sum_n W1_q . (sum_w A_q[u][w] x_q[w]) + b1_q          scale 2^(AN+AX+AW)
    h[u]   = relu(floor(a1[u] / 2^S1))                              scale 2^AH
    logit  = sum_u A_q[v][u] (W2_q . h[u]) + b2_q                   scale 2^(AN+AH+AW)  (public)
A_q = round(norm * 2^AN). Inputs x_q = round(x * 2^AX) are range-checked to the per-feature
dataset box [lo_q, lo_q + 2^BX). Worst-case bounds are checked below (p - 1) / 2.
"""
import json
import math
import sys
from collections import defaultdict

import numpy as np
import torch

sys.path.insert(0, "src")
from data import load_graph, make_splits  # noqa: E402
from model import GCN  # noqa: E402

P = 7_340_033
HALF = (P - 1) // 2
AW = 7
V = int(sys.argv[1]) if len(sys.argv) > 1 else 36333

d = load_graph()
splits = make_splits(d.y, 42)
state = torch.load("results/centralized_gcn.pt")["state_dict"]
model = GCN(d.num_node_features)
model.load_state_dict(state)
model.eval()
with torch.no_grad():
    float_logits_all = model(d.x, d.edge_index).numpy()

W1 = state["conv1.lin.weight"].numpy().T.astype(np.float64)   # 165 x 64
b1 = state["conv1.bias"].numpy().astype(np.float64)
W2 = state["conv2.lin.weight"].numpy().T.astype(np.float64)   # 64 x 2
b2 = state["conv2.bias"].numpy().astype(np.float64)
X = d.x.numpy().astype(np.float64)
src, dst = d.edge_index.numpy()
indeg = np.bincount(dst, minlength=len(X)) + 1
inn = defaultdict(list)
for a, b in zip(src, dst):
    inn[int(b)].append(int(a))


def closed_in(t):
    return [t] + inn[t]


def norm(s, t):
    return 1.0 / math.sqrt(indeg[s] * indeg[t])


U = closed_in(V)
STREAMS = sorted({w for u in U for w in closed_in(u)})
CLIP_PCT = float(sys.argv[2]) if len(sys.argv) > 2 else 0.1
# Declared preprocessing: clip each feature to its [0.1, 99.9] percentile over all nodes.
# The unbounded raw-feature box cannot fit a 23-bit field; clipping is applied identically to the
# float model below, so its accuracy cost is reported separately from the quantization cost.
lo_f, hi_f = np.percentile(X, CLIP_PCT, 0), np.percentile(X, 100 - CLIP_PCT, 0)
Xc = np.clip(X, lo_f, hi_f)


def q(w, a):
    return np.round(np.asarray(w) * 2.0 ** a).astype(np.int64)


def build(an, ax, ah, aw1=AW, aw2=AW):
    W1q, W2q = q(W1, aw1), q(W2, aw2)
    b1q, b2q = q(b1, an + ax + aw1), q(b2, an + ah + aw2)
    lo_q = np.floor(lo_f * 2.0 ** ax).astype(np.int64)
    span = (np.ceil(hi_f * 2.0 ** ax).astype(np.int64) - lo_q).max()
    bx = int(span).bit_length()
    hi_q = lo_q + 2 ** bx - 1  # range check enforces x - lo in [0, 2^bx)
    A = {u: {w: int(q(norm(w, u), an)) for w in closed_in(u)} for u in U}
    s1 = an + ax + aw1 - ah
    W1pos, W1neg = np.clip(W1q, 0, None), np.clip(W1q, None, 0)
    worst, bounds = 0, {}
    h_hi = {}
    for u in U:
        agg_hi = sum(A[u][w] * hi_q if A[u][w] >= 0 else A[u][w] * lo_q for w in A[u])
        agg_lo = sum(A[u][w] * lo_q if A[u][w] >= 0 else A[u][w] * hi_q for w in A[u])
        a_hi = W1pos.T @ agg_hi + W1neg.T @ agg_lo + b1q
        a_lo = W1pos.T @ agg_lo + W1neg.T @ agg_hi + b1q
        worst = max(worst, np.abs(a_hi).max(), np.abs(a_lo).max(), np.abs(agg_hi).max(), np.abs(agg_lo).max())
        h_hi[u] = np.maximum(np.floor_divide(a_hi, 2 ** s1), 0)
        bounds[u] = dict(a1=[int(a_lo.min()), int(a_hi.max())], q=[int(np.floor_divide(a_lo, 2 ** s1).min()), int(np.floor_divide(a_hi, 2 ** s1).max())])
    W2pos, W2neg = np.clip(W2q, 0, None), np.clip(W2q, None, 0)
    l_hi = sum(A[V][u] * (W2pos.T @ h_hi[u]) for u in U) + b2q
    l_lo = sum(A[V][u] * (W2neg.T @ h_hi[u]) for u in U) + b2q
    worst = max(worst, np.abs(l_hi).max(), np.abs(l_lo).max())
    return dict(an=an, ax=ax, ah=ah, aw1=aw1, aw2=aw2, s1=s1, bx=bx, W1=W1q, W2=W2q, b1=b1q, b2=b2q, lo_q=lo_q, hi_q=hi_q, A=A,
                bounds=bounds, logit_bounds=[l_lo.tolist(), l_hi.tolist()], worst=int(worst))


def int_forward_all(Q):
    """Integer GCN over the whole graph (same semantics), for accuracy only."""
    xq = np.clip(np.round(Xc * 2.0 ** Q["ax"]).astype(np.int64), Q["lo_q"], Q["hi_q"])
    s_all = np.concatenate([src, np.arange(len(X))])
    t_all = np.concatenate([dst, np.arange(len(X))])
    a_q = np.round(1.0 / np.sqrt(indeg[s_all] * indeg[t_all]) * 2.0 ** Q["an"]).astype(np.int64)
    xw = xq @ Q["W1"]
    agg = np.zeros((len(X), Q["W1"].shape[1]), dtype=np.int64)
    np.add.at(agg, t_all, a_q[:, None] * xw[s_all])
    h = np.maximum(np.floor_divide(agg + Q["b1"], 2 ** Q["s1"]), 0)
    hw = h @ Q["W2"]
    out = np.zeros((len(X), 2), dtype=np.int64)
    np.add.at(out, t_all, a_q[:, None] * hw[s_all])
    return out + Q["b2"]


def eval_logits(lg, mask):
    from sklearn.metrics import accuracy_score, average_precision_score, f1_score, precision_score, recall_score
    yt = d.y.numpy()[mask]
    pred = lg[mask].argmax(1)
    sc = lg[mask][:, 1] - lg[mask][:, 0]
    return dict(f1=float(f1_score(yt, pred)), precision=float(precision_score(yt, pred)), recall=float(recall_score(yt, pred)),
                accuracy=float(accuracy_score(yt, pred)), pr_auc=float(average_precision_score(yt, sc)))



def feasible(Qc):
    # requant window: rem has s1 bits, q has 22 - s1 bits; total 2^22 < p, so decomposition is unique.
    a1_abs = max(max(abs(b["a1"][0]), abs(b["a1"][1] + 1)) for b in Qc["bounds"].values())
    return Qc["worst"] < HALF and a1_abs <= 2 ** 21 and Qc["s1"] <= 21


VAL = splits["val"].numpy()
best = None
for an in range(3, 8):
    for aw1 in range(4, 8):
        for aw2 in range(4, 8):
            pick = None
            for ax in range(8, -1, -1):  # finest input scale first, then finest activation scale
                for ah in range(an + ax + aw1, -1, -1):
                    Qc = build(an, ax, ah, aw1, aw2)
                    if feasible(Qc):
                        pick = Qc
                        break
                if pick is not None:
                    break
            if pick is None:
                continue
            val = eval_logits(int_forward_all(pick).astype(np.float64), VAL)
            key = (val["pr_auc"], val["f1"])
            if best is None or key > best[0]:
                best = (key, pick)
Q = best[1]


test = splits["test"].numpy()
int_all = int_forward_all(Q)
with torch.no_grad():
    float_clipped_all = model(torch.from_numpy(Xc.astype(np.float32)), d.edge_index).numpy()
mf, mq = eval_logits(float_logits_all, test), eval_logits(int_all.astype(np.float64), test)
mfc = eval_logits(float_clipped_all, test)
agree = float(np.mean(float_logits_all[test].argmax(1) == int_all[test].argmax(1)))

# the proven instance, recomputed with the exact circuit schedule
xq = {w: np.clip(np.round(Xc[w] * 2.0 ** Q["ax"]).astype(np.int64), Q["lo_q"], Q["hi_q"]) for w in STREAMS}
h = {}
for u in U:
    agg = sum(Q["A"][u][w] * xq[w] for w in Q["A"][u])
    h[u] = np.maximum(np.floor_divide(agg @ Q["W1"] + Q["b1"], 2 ** Q["s1"]), 0)
logit = sum(Q["A"][V][u] * (h[u] @ Q["W2"]) for u in U) + Q["b2"]
assert np.array_equal(logit, int_all[V]), "instance schedule must match whole-graph integer forward"

spec = dict(
    model="H2 centralized GCN (GCNConv 165->64 ReLU, GCNConv 64->2), seed 42",
    p=P, aw1=Q["aw1"], aw2=Q["aw2"], clip_percentile=CLIP_PCT, an=Q["an"], ax=Q["ax"], ah=Q["ah"], s1=Q["s1"], qbits=22 - Q["s1"], bx=Q["bx"],
    target=V, label=int(d.y[V]), layer1_nodes=U, streams=STREAMS,
    A={str(u): {str(w): a for w, a in Q["A"][u].items()} for u in U},
    W1=Q["W1"].T.tolist(), b1=Q["b1"].tolist(), W2=Q["W2"].T.tolist(), b2=Q["b2"].tolist(),
    x_lo=Q["lo_q"].tolist(), x={str(w): xq[w].tolist() for w in STREAMS},
    h={str(u): h[u].tolist() for u in U}, logits=logit.tolist(), predicted_class=int(np.argmax(logit)),
    float_logits=float_logits_all[V].tolist(), bounds={str(k): v for k, v in Q["bounds"].items()},
    worst_abs=Q["worst"], half_p=HALF,
    accuracy=dict(float_raw=mf, float_clipped=mfc, quantized=mq, prediction_agreement_vs_raw=agree, n_test=int(test.sum())),
)
spec["selection"] = "validation PR-AUC (then F1) over feasible scales; clip percentile chosen on validation"
spec["val_metrics"] = best[0]
json.dump(spec, open(f"../h2_spec_clip{CLIP_PCT}.json", "w"))
print(json.dumps(dict(aw1=Q["aw1"], aw2=Q["aw2"], float_clipped=mfc, an=Q["an"], ax=Q["ax"], ah=Q["ah"], s1=Q["s1"], bx=Q["bx"], worst=Q["worst"], half=HALF,
                      U=U, streams=STREAMS, bounds=spec["bounds"], logits=spec["logits"], float_logits=spec["float_logits"],
                      label=spec["label"], float=mf, quantized=mq, agreement=agree), indent=1))
