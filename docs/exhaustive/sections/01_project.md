# Part 1 — The programme

## 1.1 Source proposal

The proposal sets out a three-phase research programme for a CBDC stack in which AI and blockchain coexist. It targets three structural contradictions:

1. **The privacy–compliance paradox.** Privacy tools blind AML systems, and AML analytics erode privacy. In Elliptic, 77% of transactions are unlabelled and about 2% are confirmed illicit.
2. **The algorithmic liability void.** No enforceable, automatic liability exists for autonomous AI agents acting in a CBDC network.
3. **The determinism–adaptability trade-off.** Consensus needs determinism; AI is probabilistic and too expensive to run on-chain.

The proposal decomposes its objectives into four falsifiable hypotheses, each with a stated failure criterion:

| ID | Hypothesis (proposal wording, condensed) | Proposal's failure criterion |
|---|---|---|
| H1 | Restructuring AML/credit inference as a bisectable ONNX graph under an optimistic dispute protocol (zk-OPML) cuts proof-generation latency **and** on-chain verification cost by ≥ 10× versus monolithic ZKML, with ≤ 1 pp accuracy loss | Bisection fails to isolate the disputed operator in bounded rounds on deep models, or the reduction is below one order of magnitude |
| H2 | Blockchain-orchestrated federated learning across simulated institutions gives a statistically significant ≥ 20% reduction in false-positive AML flags versus a centralized baseline on the same label-scarce data, with PII leakage formally bounded by DP (target ε) | The reduction is not significant, or the ε needed is unacceptable |
| H3 | Staked, non-transferable ABTs with coded slashing measurably reduce policy-violating agent actions versus unstaked control agents | No significant deterrence, **or** slashing can be circumvented by Sybil-style re-identification |
| H4 | A lattice-based ZK-STARK can secure ZKML verification for the H1–H3 model classes with proof size and verifier latency within a bounded overhead factor versus classical STARKs | The overhead exceeds the bound needed for (near-)real-time verification |

**Out of scope for this programme** (proposal items that were not attempted):
- deep-RL monetary policy;
- the Reverse-Kelly AMM;
- ExpProof-style verifiable explanations;
- secure multi-party aggregation and blockchain orchestration of the federated-learning layer;
- an on-chain ABT contract;
- settlement-layer integration.

See §7.4.

## 1.2 Team and responsibilities

Each hypothesis was owned end to end by one student. The mentor reviewed and merged every PR, audited the work, and in the final phase carried out the corrective and extension work recorded in Part 7.

| Hypothesis | Owner | Folder |
|---|---|---|
| H1 | Aman Hooda | `h1-zkopml-v2/` (v1 was `h1-zkopml/`, deleted 2026-09-19, recovered from git for this report) |
| H2 | Indrakshi | `h2-federated-aml/` |
| H3 | Anjali | `h3-abt/` |
| H4 | Simran Pundir | `h4-pqc-stark/` |

## 1.3 Process

- **Monorepo:**
  - one folder per hypothesis, no shared code;
  - branches named `<name>/<topic>`;
  - every change goes through a PR into `main` and is reviewed before merge.
- **Pre-registration (from 2026-10-06):** parameters, bounds and analysis code are committed before a confirmatory run. Post-hoc items are labelled as such, and a failed pre-registered result keeps its verdict even if a later version passes.
- **Statistics:** the unit of analysis is the seed (H2) or the agent (H3); paired designs are used where possible; Holm correction is applied across levels or primary tests.
- **Audit (2026-10-06):** a repository-wide audit found:
  - an unsound H4 circuit;
  - an H2 design that could not test its own criterion;
  - pseudo-replication in the H3 statistics;
  - missing caveats in H1.

  Each was corrected through PRs #13–#22.

## 1.4 Timeline

{{gen:commit_count}}

| Date | Milestone |
|---|---|
| 2026-09-03 | Repository created: folders, READMEs, proposal |
| 2026-09-06 – 09-11 | First submissions: H1 v1 (partial proving), H3 protocol and baseline, H2 v1 code, H4 classical baseline |
| 2026-09-11 – 09-19 | H1 v2 (bisection), H3 staking, H2 v1 results, H4 v1 lattice prototype (PR #9), H3 statistical analysis and Sybil probe |
| 2026-09-22 | H4 research-candidate SIS parameters |
| 2026-10-06 | Audit. H3 confirmatory recorded, H1 caveats, H2 status correction; H4 confirmatory pre-registered and **halted** (soundness defect) |
| 2026-10-07 – 10-10 | PR #18 (H4 fix attempt) closed; H2 5-seed results (PR #19); H3 agent-level re-analysis |
| 2026-10-11 | H4 v2 sound circuit; H2 protocol v2; lattice estimator; H4 v3; H1–H3 model coverage; overhead law; H2 matched-recall; composed PQ proof |
| 2026-10-13 | H3 durable liability; full reports |

## 1.5 Every pull request *(generated)*

{{gen:pr_history}}
