"""Cross-check the independent-validator run (20-21 Oct 2026) from the two sides' own records.

reviewer/ : what the independent validator decided (its decisions.jsonl) and its check against its own ledger
host/     : the host's run records (runs/*/transactions.csv, summary.json) and its three validators' decision logs

Checks, for the run that settled (independent_200_b):
  1. the transfers that settled are exactly the ones the reviewer signed, and exactly the honest ones;
  2. every settled transfer carries 4 signatures, every falsified one 0;
  3. each of the host's validators saw the same request ids and made the same decision as the reviewer;
  4. the reviewer's refusal reason matches the kind of falsification injected;
and for the first attempt (independent_200), when the reviewer's validator was unreachable: nothing settled.

Usage: python3 cross_check.py      (standard library only)
"""
import csv
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
EXPECTED_REASON = {
    "wrong_model": "statement is about a different model",
    "amount_swap": "commitment does not open to the stated amount",
    "stale_slash": "proof used slash",
    "corrupt_proof": "zero-knowledge proof does not verify",
    "flip_viol": "zero-knowledge proof does not verify",
}


def rows(run):
    return {int(r["i"]): r for r in csv.DictReader(open(HERE / "host" / "runs" / run / "transactions.csv"))}


def main():
    rev = [json.loads(line) for line in open(HERE / "reviewer" / "decisions.jsonl")]
    run_start = rev[0]["run_start"]
    b = rows("independent_200_b")
    signed = {d["i"] for d in rev if d["signed"]}
    settled = {i for i, r in b.items() if r["outcome"] != "NotSubmitted"}
    honest = {i for i, r in b.items() if not r["fault"]}
    report = {
        "reviewer_decisions": len(rev), "reviewer_signed": len(signed), "reviewer_refused": len(rev) - len(signed),
        "host_honest": len(honest), "host_falsified": len(b) - len(honest), "host_settled": len(settled),
        "settled_equals_reviewer_signed": settled == signed,
        "settled_equals_honest": settled == honest,
        "signatures_on_settled": dict(Counter(b[i]["signatures"] for i in settled)),
        "signatures_on_falsified": dict(Counter(b[i]["signatures"] for i in b if b[i]["fault"])),
        "reason_matches_falsification": all(EXPECTED_REASON[b[d["i"]]["fault"]] in d["reason"] for d in rev if not d["signed"]),
        "refusals_by_falsification": dict(Counter(b[d["i"]]["fault"] for d in rev if not d["signed"])),
    }
    for v in range(3):
        log = {d["i"]: d for d in map(json.loads, open(HERE / "host" / "logs" / f"decisions_v{v}.jsonl")) if d["run_start"] == run_start}
        report[f"host_validator_{v}_same_request_ids_and_decisions"] = (
            len(log) == len(rev) and all(log[d["i"]]["request_id"] == d["request_id"] and log[d["i"]]["signed"] == d["signed"] for d in rev))
    a = rows("independent_200")
    report["first_attempt_reviewer_unreachable_settled"] = sum(r["outcome"] != "NotSubmitted" for r in a.values())
    report["reviewer_own_ledger_check"] = {k: v for k, v in json.load(open(HERE / "reviewer" / "my_check.json")).items() if k.startswith("check")}
    report["all_ok"] = (report["settled_equals_reviewer_signed"] and report["settled_equals_honest"]
                        and report["reason_matches_falsification"] and report["first_attempt_reviewer_unreachable_settled"] == 0
                        and all(report[f"host_validator_{v}_same_request_ids_and_decisions"] for v in range(3))
                        and all(v is True for v in report["reviewer_own_ledger_check"].values()))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
