"""Quantize the trained H1 model (W32_B1) to exact integer semantics that fit F_p, p = 7,340,033.

Semantics (what the circuit proves, bit for bit):
    x_q   = clip(round(x * 2^AX), lo_q, hi_q)                    per feature, x = scaled input
    a1    = W1_q . x_q + b1_q                                    scale 2^(AX+AW)
    h     = relu(floor(a1 / 2^S1))                               scale 2^AH,  S1 = AX + AW - AH
    a2    = W2_q . h + b2_q                                      scale 2^(AH+AW)
    h2    = h + relu(floor(a2 / 2^AW))                           scale 2^AH
    logit = W3_q . h2 + b3_q                                     scale 2^(AH+AW)   (public output)
    flag  = logit > T_q,   T_q = floor(logit(threshold) * 2^(AH+AW))
Every intermediate is bounded by interval propagation over the clipped input box, and the
bounds are checked to stay below (p - 1) / 2, so no value wraps for ANY valid input.
"""
import json
import math
import pickle

import numpy as np
from sklearn.metrics import accuracy_score, average_precision_score, f1_score, precision_score, recall_score

from h1_data import apply_scaler, load, temporal_split

P = 7_340_033
HALF = (P - 1) // 2
AW = 7

m = pickle.load(open("W32_B1.pkl", "rb"))
sc = m["scaler"]
(Wp, bp), (Wb, bb), (Wh, bh) = [(u["W"], u["b"]) for u in m["units"]]
thr = m["res"]["threshold"]
lo_f = (sc["lo"] - sc["center"]) / sc["scale"]
hi_f = (sc["hi"] - sc["center"]) / sc["scale"]


def q(w, a):
    return np.round(w * 2.0 ** a).astype(np.int64)


def build(ax, ah):
    lo_q, hi_q = np.round(lo_f * 2.0 ** ax).astype(np.int64), np.round(hi_f * 2.0 ** ax).astype(np.int64)
    W1, W2, W3 = q(Wp, AW), q(Wb, AW), q(Wh, AW)
    b1, b2, b3 = q(bp, ax + AW), q(bb, ah + AW), q(bh, ah + AW)
    s1 = ax + AW - ah
    # interval bounds (worst case over the clipped input box)
    W1pos, W1neg = np.clip(W1, 0, None), np.clip(W1, None, 0)
    a1_hi = (W1pos.T @ hi_q + W1neg.T @ lo_q) + b1
    a1_lo = (W1pos.T @ lo_q + W1neg.T @ hi_q) + b1
    h_hi = np.maximum(np.floor_divide(a1_hi, 2 ** s1), 0)
    W2pos, W2neg = np.clip(W2, 0, None), np.clip(W2, None, 0)
    a2_hi = W2pos.T @ h_hi + b2
    a2_lo = W2neg.T @ h_hi + b2
    h2_hi = h_hi + np.maximum(np.floor_divide(a2_hi, 2 ** AW), 0)
    W3pos, W3neg = np.clip(W3, 0, None), np.clip(W3, None, 0)
    a3_hi = W3pos.T @ h2_hi + b3
    a3_lo = W3neg.T @ h2_hi + b3
    worst = max(np.abs(a1_hi).max(), np.abs(a1_lo).max(), np.abs(a2_hi).max(), np.abs(a2_lo).max(),
                np.abs(a3_hi).max(), np.abs(a3_lo).max(), np.abs(h2_hi).max())
    return dict(ax=ax, ah=ah, s1=s1, lo_q=lo_q, hi_q=hi_q, W1=W1, W2=W2, W3=W3, b1=b1, b2=b2, b3=b3,
                bounds=dict(a1=[int(a1_lo.min()), int(a1_hi.max())], a2=[int(a2_lo.min()), int(a2_hi.max())],
                            a3=[int(a3_lo[0]), int(a3_hi[0])], h=int(h_hi.max()), h2=int(h2_hi.max())),
                worst=int(worst))


def forward(Q, X):
    xq = np.clip(np.round(X * 2.0 ** Q["ax"]).astype(np.int64), Q["lo_q"], Q["hi_q"])
    a1 = xq @ Q["W1"] + Q["b1"]
    h = np.maximum(np.floor_divide(a1, 2 ** Q["s1"]), 0)
    a2 = h @ Q["W2"] + Q["b2"]
    h2 = h + np.maximum(np.floor_divide(a2, 2 ** AW), 0)
    logit = (h2 @ Q["W3"] + Q["b3"])[:, 0]
    return xq, a1, h, a2, h2, logit


best = None
for ax in range(2, 9):
    for ah in range(2, ax + AW + 1):
        Q = build(ax, ah)
        if Q["worst"] < HALF and (best is None or (ax, ah) > (best["ax"], best["ah"])):
            best = Q
Q = best
X, y, ts = load()
(Xtr, ytr), (Xva, yva), (Xte, yte), _ = temporal_split(X, y, ts)
Xte_s = apply_scaler(Xte, sc)
# float reference (same forward as H1 train.py)
h = np.maximum(Xte_s @ Wp + bp, 0)
h = h + np.maximum(h @ Wb + bb, 0)
logit_f = (h @ Wh + bh)[:, 0]
prob_f = 1 / (1 + np.exp(-logit_f))
_, _, _, _, _, logit_q = forward(Q, Xte_s)
T_q = math.floor(math.log(thr / (1 - thr)) * 2.0 ** (Q["ah"] + AW))
prob_q = 1 / (1 + np.exp(-logit_q / 2.0 ** (Q["ah"] + AW)))


def metrics(pred, score):
    return dict(f1=float(f1_score(yte, pred)), precision=float(precision_score(yte, pred)), recall=float(recall_score(yte, pred)),
                accuracy=float(accuracy_score(yte, pred)), pr_auc=float(average_precision_score(yte, score)))


mf, mq = metrics(prob_f > thr, prob_f), metrics(logit_q > T_q, prob_q)
agree = float(np.mean((prob_f > thr) == (logit_q > T_q)))
# example: first illicit test transaction that both models flag
idx = int(np.where((yte == 1) & (logit_q > T_q) & (prob_f > thr))[0][0])
xq, a1, hq, a2, h2q, lg = forward(Q, Xte_s[idx:idx + 1])
spec = dict(
    model="H1 W32_B1 (proj 165->32 ReLU, residual block 32->32 ReLU, head 32->1)",
    p=P, aw=AW, ax=Q["ax"], ah=Q["ah"], s1=Q["s1"], s2=AW,
    W1=Q["W1"].T.tolist(), b1=Q["b1"].tolist(), W2=Q["W2"].T.tolist(), b2=Q["b2"].tolist(), W3=Q["W3"].T.tolist(), b3=Q["b3"].tolist(),
    x_lo=Q["lo_q"].tolist(), x_hi=Q["hi_q"].tolist(), bounds=Q["bounds"], worst_abs=Q["worst"], half_p=HALF,
    threshold_q=T_q, example_index_in_test=idx, example_label=int(yte[idx]),
    x=xq[0].tolist(), h=hq[0].tolist(), h2=h2q[0].tolist(), logit=int(lg[0]), flagged=bool(lg[0] > T_q),
    accuracy=dict(float=mf, quantized=mq, decision_agreement=agree, n_test=int(len(yte))),
)
json.dump(spec, open("h1_spec.json", "w"))
print(json.dumps(dict(ax=Q["ax"], ah=Q["ah"], s1=Q["s1"], worst=Q["worst"], half=HALF, bounds=Q["bounds"], T_q=T_q,
                      float=mf, quantized=mq, agreement=agree, example=idx, logit=int(lg[0])), indent=1))
