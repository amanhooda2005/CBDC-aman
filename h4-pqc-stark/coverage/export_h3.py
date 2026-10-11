"""Export a batch of real H3 decisions (staked treatment, confirmatory config, seed 42).

Fixed-point semantics proven by the circuit (scale Q = 2^8 for money, detection p scaled 2^8):
    viol    = (amount > max_compliant) OR (destination == sanctioned_id)
    active  = is_staked AND stake > 0
    mn      = min(slash, stake)
    es      = active * p * mn                          (scale 2^16)
    execute = viol ? (violation_payoff * 2^8 - es) : compliant_payoff * 2^8
    EXEC    = execute > compliant_payoff * 2^8         (tie -> refuse)
"""
import json
import sys

sys.path.insert(0, sys.argv[1])
from h3_abt.config import load_config  # noqa: E402
from h3_abt.treatment import run_staked_treatment  # noqa: E402
from h3_abt.types import Action  # noqa: E402

Q = 256
cfg = load_config(sys.argv[2])
records = run_staked_treatment(cfg).records
SANCTIONED = 1000


def dest_id(name):
    return SANCTIONED if name in cfg.sanctioned_destinations else int(name.split("_")[1])


def q(v):
    return int(round(v * Q))


M, SLASH, P = q(cfg.max_compliant_amount), q(cfg.slash_amount), q(cfg.detection_probability)


def decide(r):
    a, d, c, vp, s = q(r.amount), dest_id(r.destination), q(r.compliant_payoff), q(r.violation_payoff), q(r.stake_before)
    viol = int(a > M or d == SANCTIONED)
    active = int(r.is_staked and s > 0)
    es = active * P * min(SLASH, s)
    execute = (vp * Q - es) if viol else c * Q
    return dict(amount=a, dest=d, compliant=c, violation=vp, staked=int(r.is_staked), stake=s,
                viol=viol, exec=int(execute > c * Q))


rows = [decide(r) for r in records]
agree = sum((row["exec"] == 1) == (r.action is Action.EXECUTE) for row, r in zip(rows, records)) / len(rows)
viol_agree = sum(row["viol"] == int(r.offer_is_policy_violation) for row, r in zip(rows, records)) / len(rows)
batch = rows[:64]
spec = dict(
    model="H3 agent decision rule (protocol.choose_action + is_violation), staked treatment, seed 42",
    q=Q, max_compliant=M, slash=SLASH, p_detect=P, sanctioned_id=SANCTIONED,
    decisions=batch,
    agreement=dict(action_vs_float=agree, violation_flag_vs_float=viol_agree, n=len(rows)),
)
json.dump(spec, open("h3_spec.json", "w"))
print(json.dumps(dict(n_records=len(rows), action_agreement=agree, violation_agreement=viol_agree,
                      batch_exec=sum(r["exec"] for r in batch), batch_viol=sum(r["viol"] for r in batch),
                      max_amount=max(r["amount"] for r in rows), max_stake=max(r["stake"] for r in rows),
                      max_violation=max(r["violation"] for r in rows)), indent=1))
