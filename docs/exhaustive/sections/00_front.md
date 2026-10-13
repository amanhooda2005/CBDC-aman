# CBDC – AI – Blockchain Research Programme: Exhaustive Record of Every Experiment

**Repository:** github.com/maanas1234/CBDC (`main`)
**Team:** Aman Hooda (H1), Indrakshi (H2), Anjali (H3), Simran Pundir (H4). Mentor and reviewer: Maanas Tyagi.
**Source proposal:** *Architecting a Secure, Compliant, and Intelligent CBDC Ecosystem* (`docs/CBDC_AI_Blockchain_Research_Proposal.pdf`)
**Companion summary:** `docs/FULL_REPORT.md` (short version). This document is the long version.

---

## How this document is built and how to read it

This is the complete record of the programme. It covers:
- every experiment that was run, including versions later withdrawn or superseded;
- every result table;
- every pre-registration;
- every correction and every closed pull request.

Nothing that was run is left out.

**Generated tables.** Tables marked *(generated)* are produced by `docs/exhaustive/build.py` directly from the committed result files (CSV/JSON) at build time. No number in them was retyped by hand. To rebuild, run `python docs/exhaustive/build.py` from the repository root.

**Hand-written text.** The narrative text was written for this document. Where a number appears in the narrative, it is quoted from a generated table or from a committed write-up, and the source is named.

**Status labels.** Every result carries one of these labels:

| Label | Meaning |
|---|---|
| **Confirmatory, pre-registered** | Parameters, bounds and analysis code were committed to git before the run; the commit is named. |
| **Exploratory** | Run without pre-registration; reported for completeness. |
| **Post-hoc** | Defined or analysed after seeing the data. |
| **Superseded** | Replaced by a later, better-designed version. Kept for the record. |
| **Withdrawn** | Found to be invalid (for example, an unsound circuit). Its numbers must not be cited. |

**Structure:**

| Part | Content |
|---|---|
| 1 | Programme: proposal, hypotheses, team, process, timeline, every PR |
| 2 | H1: verifiable off-chain inference (v1 partial proving; v2 bisection) |
| 3 | H2: federated AML under label scarcity (v1, 5-seed v1, protocol v2, matched-recall test) |
| 4 | H3: AgentBound Tokens (baseline, treatment, confirmatory, agent-level, Sybil, durable liability) |
| 5 | H4: post-quantum ZK-STARK (v1 prototypes, halt, v2, v3, model coverage, composed proof, overhead law) |
| 6 | Cross-cutting findings and the system picture |
| 7 | Corrections, deviations, withdrawn work, and what was not done |
| 8 | Reproducibility and the full artifact index |
| Appendices | Configurations, pre-registrations and original documentation, verbatim |

## Glossary

| Term | Meaning |
|---|---|
| AML / CFT | Anti-money-laundering / countering the financing of terrorism |
| CBDC | Central bank digital currency |
| Elliptic | Public Bitcoin transaction graph dataset: 203,769 transactions, 234,355 edges, 49 time steps. 4,545 transactions are labelled illicit, 42,019 licit, and the rest unknown. |
| ZKML | Zero-knowledge proofs of machine-learning inference |
| zk-OPML | Optimistic ML with zero-knowledge dispute resolution: results are assumed correct unless challenged; a challenge is settled by a targeted proof |
| EZKL | Halo2/KZG-based ZKML toolchain (version 23.0.5 used) |
| Bisection | Binary search over committed intermediate results to find the first step where two parties disagree |
| FedAvg | Federated averaging: each institution trains locally; the server averages the model weights |
| GCN | Graph convolutional network |
| DP, ε, δ | Differential privacy and its privacy-loss parameters |
| RDP | Rényi differential privacy (used here for accounting) |
| Recall / FP | Share of illicit transactions caught / number of licit transactions wrongly flagged |
| Matched recall | Comparing models at thresholds that give them the same recall |
| ABT | AgentBound Token: a non-transferable token binding an AI agent to staked collateral and a violation history |
| Slashing | Automatic confiscation of stake when a violation is detected |
| Sybil attack | Escaping penalties by creating a new identity |
| Judgment-proof | An agent with nothing left to lose: stake fully slashed, so the deterrent is gone |
| STARK | Hash-based transparent proof system; plausibly post-quantum |
| AIR | Algebraic intermediate representation: the constraint system a STARK proves |
| SIS | Short Integer Solution: lattice problem behind the commitment c = G·bits + B·r mod p |
| PQ | Post-quantum |
| RLC | Random linear combination (the v3 proof technique) |
| Winterfell | Rust STARK library (fork `bwesterb/winterfell@b964cfd`, field f23201, p = 7,340,033) |
| Holm | Holm–Bonferroni correction for multiple tests |
| Wilcoxon / Mann–Whitney | Rank-based paired / unpaired tests |
