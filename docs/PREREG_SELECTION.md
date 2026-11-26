# Pre-registration: envelope-informed packet selection (experiment 1)

> **Status: pre-registration. Non-normative.** It changes no wire behaviour, no schema, no
> `SPEC.md` text, and no rule in `experiments/timeout_recovery/run.py`.
>
> **No runs before merge.** No run of this experiment, including the pilot in §13, happens
> before the maintainer (`robotmarvin0706`) merges this file into `main`. The rules
> below are frozen at that merge. They are not edited after any data has been seen.
>
> Background: [`docs/WP3_FINDINGS.md`](WP3_FINDINGS.md),
> [`experiments/timeout_recovery/WP3_RESULTS.md`](../experiments/timeout_recovery/WP3_RESULTS.md),
> [`docs/LLM_RECEIVER_OBSERVATION.md`](LLM_RECEIVER_OBSERVATION.md).

## 1. Why a new experiment

WP3 compared `protocol` with `plain_file_gated`. The paired ΔSR was 0.000 with a CI of
[0, 0] on all four seeds. That zero is an identity, not a measurement: both arms call
`parse_recovery_params(content)` and then the same deterministic gate, and no envelope
field (`id`, `packet_ref`, `status`, `version`) enters `success = simulate(gate(policy,
params))`. Single-packet immediate ΔSR is an endpoint for the **content** layer. It cannot
decide anything about the **protocol** layer.

This experiment measures a quantity that is not zero by construction: the difference
between a receiver that **uses envelope fields to decide which packets to absorb** and one
that receives the same envelopes and ignores them. Content, parser, gate and host are the
same in both arms. Only the selection decision differs.

The question, after the fixes in §2: **under a fixed absorption budget, with noisy
`prior_outcomes` and one peer that lies about its outcomes, does envelope-informed
selection reach a higher plateau success rate than envelope-blind selection?**

## 2. Changes from the design draft

This file is the English, repository version of the protocol-layer measurement draft of
2026-11-26, with the required fixes from the review of the same date applied. Where the
draft and the review disagree, the review is followed.

| Review item | Where it is applied |
| --- | --- |
| 1. The falsification check must ablate the envelope, not the content: shuffle envelope fields across packets, keep content identical; D must collapse to C. Keep the content stub as a separate sanity check. | §11.2 (V2, envelope shuffle) and §11.1 (V1, content stub) |
| 2. `prior_outcomes` must not be computed from the scoring ground truth: pre-registered noise model, and at least one dishonest peer whose self-reports are good while its packets are harmful. Report harmful absorption per arm. | §6.2 (noise model), §6.3 (dishonest peer), §8.2 (harmful absorption) |
| 3a. One margin, at or above the noise floor, used everywhere. | §10: δ = 0.10, used in §8.3, §11, §12 |
| 3b. Arm mapping table A/B/C/D ↔ selection arms; name the primary contrast. | §4 |
| 3c. Power calculation for ΔSR = 0.10; raise N if insufficient. | §9: N raised from 4 runs to 48 runs |
| 3d. Operational plateau definition, hence `tasks_to_plateau` and `tokens_to_plateau`. | §8.3 |
| 3e. Equivalence is worded "equivalent within ±δ", never "no measurable increment" or "protocol has no effect". | §12, §14 |
| 4a. A minimal independent second implementation that passes conformance, built in parallel. | §15.1 |
| 4b. The expiry experiment runs right after this one, before the budget experiment. | §15.2 |
| Housekeeping: English only; repo-relative links; open a PR, the maintainer merges. | This file |

Other changes made while turning the draft into a runnable specification are listed
where they occur and collected in §16.

## 3. Layers: gates and effect sizes

| Layer | Claim | Form | Status at registration |
| --- | --- | --- | --- |
| Precondition: exchangeable | Two independent implementations interoperate | Boolean: an external implementation passes `tests/conformance/` (§15.1) | Not met (see §15.1) |
| Precondition: verifiable | Absorption has evidence and can be rolled back | Boolean: `absorbed` carries `effect_summary`; rollback success 100% | Met |
| Precondition: attributable | Every policy change traces to a packet | Boolean: attribution rate 1.0 | Met |
| **Target: capability growth** | Exchange makes the receiver better | **Effect size on the cumulative capability curve** | This experiment |

Preconditions are entry gates, not findings. The target is an effect size with its own
endpoint and counterfactual. They are reported separately and never combined into one
verdict.

## 4. Arms

### 4.1 Mapping table

| Label | Arm name in code and results | Packets delivered | Envelope delivered | Selection uses envelope | Selection rule (§7) | Isolates |
| --- | --- | --- | --- | --- | --- | --- |
| A | `no_sharing` | none | — | — | — | baseline |
| B | `local_file_gated` | same pool, content only, as file text | no | no | `random_selection` | value of the **content** (B − A) |
| C | `protocol_blind` = `random_selection` | same pool | yes, full packet over the protocol path | no | `random_selection` | **packaging cost** (C − B, tokens only) |
| D1 | `protocol_informed_history` = `history_selection` | same pool | yes | yes: `prior_outcomes`, `provenance`, own attributed Absorption Results | `history_selection` | **envelope value** (D1 − C), **primary** |
| D2 | `protocol_informed_claim` = `claim_selection` | same pool | yes | yes: `verification_claim` | `claim_selection` | envelope value (D2 − C), secondary |
| V1 | `protocol_blind_stub` | same pool | yes | no | `random_selection`, same order as C | content-stub sanity check (§11.1) |
| V2 | `protocol_informed_history_shuffled` | same pool, content unchanged | yes, envelope fields permuted across packets | yes | `history_selection` | envelope-shuffle falsification (§11.2) |

- **Primary contrast: D1 − C** (`history_selection − random_selection`) on `naive_host`.
  It is the only contrast that can produce the headline verdict (§12).
- Secondary contrasts, reported with the same rules but not used for the headline: D2 − C;
  every contrast on `working_host`; B − A; C − B (tokens).
- B and C share the random selection order and the same gate, so their SR is identical by
  design. C − B is a token cost, as in WP3.

### 4.2 Hosts

All arms run on both hosts from `experiments/timeout_recovery/host.py`, unchanged:

| Host | Starting policy | Role |
| --- | --- | --- |
| `naive_host` | `backoff=none,max_attempts=3,drop_stale=False` | **primary**: effective packets can raise SR, so selection can matter |
| `working_host` | `backoff=exp400ms,max_attempts=4,drop_stale=True` | secondary: there is little to gain, and harmful packets are visibly harmful if absorbed |

## 5. Environment

One **run** is one receiver on one host working through one task sequence with one
packet pool. All arms in a run share the run's task draws, pool, prior outcomes and
random-selection order (paired design).

- **Tasks.** 200 tasks, T1…T200. Each task is one case from the existing case generator
  (`Case`, `run_window`) with 3 `immediate` and 3 `delayed` evaluation episodes, as in
  WP0–WP3. Task SR is the mean success over its 6 episodes. The receiver's current policy
  carries over from task to task.
- **Absorption slots.** 10 slots, at the start of tasks 1, 16, 31, …, 136 (every 15 tasks).
  At each slot the arm selects one untried packet from the pool and sends it through the
  host's gate: content policy (SPEC §2.1), isolation (`ISOLATION_N = 3` trials,
  `DSR_THRESHOLD = 0.05` against the **current** policy), single-step rollback, and the §3.1
  hard gate. A rejected, isolated or rolled-back packet still uses its slot. The budget is
  10 attempts out of a pool of 50.
- **No absorption in T151–T200.** The last 50 tasks are the plateau window (§8.1).
- **Gate constants** are those in `host.py` at the merge commit of this file and are not
  changed for this experiment.

## 6. Peers, pool and envelope fields

### 6.1 Peers and packet content

Five simulated peers, ten packets each, 50 packets per run. Packet content uses the
existing `recovery_policy` format of `experiments/timeout_recovery/packets.py`. Each
packet's parameters are drawn per run from its peer's family:

| Peer | Count | Family (per-packet draw) | Honest reports |
| --- | --- | --- | --- |
| `peer-eff-1`, `peer-eff-2` | 2 | effective: exponential backoff, `base_backoff_ms` ∈ {200, 400, 800}, `max_attempts` ∈ {3, 4, 5}, `drop_stale_partials` = true with probability 0.8 | yes |
| `peer-med-1`, `peer-med-2` | 2 | mediocre: exponential backoff, `base_backoff_ms` ∈ {50, 100}, `max_attempts` ∈ {2, 3}, `drop_stale_partials` = false | yes |
| `peer-dis-1` | 1 | harmful: the `wrong_packet` family, `backoff` = none, `max_attempts` ∈ {6, "unbounded"}, `drop_stale_partials` = false | **no** (§6.3) |

Every pool packet must pass `content_policy` (SPEC §2.1). A `content_policy:` rejection of
a pool packet is a bug in the generator, and the run is discarded and fixed before the
main run, not counted.

### 6.2 Noise model for `prior_outcomes` (honest peers)

`prior_outcomes` must not be derived from the ground truth that scores the receiver.
For each honest peer's packet:

1. `m`, the number of earlier receivers, is drawn uniformly from {3, 4, …, 12}.
2. Each earlier receiver is a fresh `naive_host` case drawn from an **independent random
   stream** (seed parts `("prior", seed, run, packet_id, j)`), never used for any scored
   task. The packet goes through the same gate on that case; the outcome is `absorbed` or
   not.
3. Each outcome is then **flipped with probability φ = 0.15** (reporting noise).
4. `absorbed_count` is the number of `absorbed` outcomes after flipping,
   `rejected_count = m − absorbed_count`, and `last_status` is the status of the `m`-th
   outcome (`absorbed` or `rejected`).

So `prior_outcomes` is correlated with the packet's true value on the receiver (it comes
from the same family of hosts) but it is a small, noisy sample from different cases.

Honest `verification_claim`: `metric = "recovery_sr"`, `value` = the packet's mean SR over
30 further independent `naive_host` cases from the `prior` stream, `repro = {seed, n: 30,
model: "deterministic-rule-host-v1"}`, and a short `expected_effect` text.

`provenance.source_ref` is the peer id (for example `peer:peer-eff-1`).

### 6.3 The dishonest peer

`peer-dis-1` reports good outcomes for packets that are harmful:

- `absorbed_count` ~ Binomial(m, 0.9) with `m` drawn as in §6.2, `rejected_count = m −
  absorbed_count`, `last_status = "absorbed"`.
- `verification_claim.value` drawn uniformly from [0.85, 0.95], `repro = {seed, n: 30,
  model: "deterministic-rule-host-v1"}`. The claim is fabricated; nothing was run.
- `provenance.source_ref = "peer:peer-dis-1"` (it does not impersonate another peer).

The experiment therefore asks whether the envelope still helps when one peer in five lies.
`history_selection` can only recover from the lie through attribution of the receiver's own
Absorption Results (§7.2). `claim_selection` has no such mechanism and is expected to be
misled; that is reported, not hidden.

## 7. Selection rules

Ties are broken with a seeded RNG (`("tie", seed, run, slot)`). A packet is never tried
twice in a run.

### 7.1 `random_selection` (C, B, V1)

A uniform random permutation of the 50 packets, drawn once per run (`("order", seed,
run)`). Slot *k* takes the *k*-th packet. B, C and V1 use the same permutation.

### 7.2 `history_selection` (D1, V2)

For each untried packet *p* with `provenance.source_ref` = *s*:

```
a = prior_outcomes.absorbed_count(p) + 3 × own_absorbed(s)
r = prior_outcomes.rejected_count(p) + 3 × own_not_absorbed(s)
score(p) = (a + 1) / (a + r + 2)
```

`own_absorbed(s)` counts the receiver's own Absorption Results in this run with status
`absorbed` (and not rolled back) for packets attributed to source *s*;
`own_not_absorbed(s)` counts `rejected`, `isolated` and rolled-back results attributed to
*s*. The weight 3 is fixed here and is not tuned. The highest score is selected.

### 7.3 `claim_selection` (D2)

Eligible packets have `verification_claim.metric == "recovery_sr"` and
`verification_claim.repro.n >= 30`. Eligible packets are ranked by
`verification_claim.value`, descending; ineligible packets follow in random order.

### 7.4 Token accounting for selection

Arms that read envelopes are charged for it: at each slot, D1, D2 and V2 are charged
`approx_tokens` of the envelopes (every field except `content`) of all untried packets.
B, C and V1 are charged nothing for selection. This charge is part of
`tokens_to_plateau`.

## 8. Endpoints

### 8.1 Primary endpoint

**`plateau_SR`**: the mean task SR over T151–T200 (the final 50 tasks, no absorption slot
inside). It is fixed in advance and does not depend on plateau detection.

### 8.2 Secondary endpoints (reported, no verdict)

- `tasks_to_plateau` and `tokens_to_plateau` (§8.3).
- `AUC`: the mean of the eight window SRs (§8.3), i.e. the normalised area under the
  cumulative capability curve.
- **Harmful absorption per arm**: packets from `peer-dis-1` selected, absorbed, and rolled
  back, per run and in total. Also the same for mediocre packets.
- Slots used before the first absorbed effective packet.
- Total tokens over T1–T200, and the selection-reading tokens of §7.4 separately.
- Attribution rate (policy changes traceable to a packet id with a matching Absorption
  Result). Process metric, not capability.

### 8.3 Plateau definition

- **Windows**: 8 windows of 25 tasks, W1 = T1–T25, …, W8 = T176–T200. `SR(Wi)` is the mean
  task SR in the window.
- **Reference**: `plateau_SR` (§8.1), which equals the mean of `SR(W7)` and `SR(W8)`.
- **Tolerance**: δ = 0.10 (§10). Two standard errors of a 25-task window mean are about
  0.10 at the observed per-task SD.
- **Plateau reached at window w**: the smallest w such that `SR(Wj) >= plateau_SR − δ` for
  j = w, w+1, w+2 (three consecutive windows). For w = 7 the condition uses W7 and W8 only;
  for w = 8, W8 only.
- **`tasks_to_plateau`** = 25 × w (the last task of window w).
- **`tokens_to_plateau`** = all tokens charged to the arm from T1 through task
  `tasks_to_plateau`: task episodes, absorption, isolation and rollback checks, and the
  selection reading of §7.4.
- If no w qualifies (only possible when W7 and W8 differ by more than 2δ), the run is
  reported as `plateau_not_reached`, with `tasks_to_plateau = 200` marked as censored.

## 9. Sample size and power

The unit of analysis is the **run** (§5). Tasks inside a run share one sequence of
selection decisions, so they are not independent replicates of the selection effect.

**Variance model.** The paired difference of `plateau_SR` between two arms in one run has
variance `σ_b² + σ_t² / 50`:

- `σ_t` is the per-task paired SD. In the committed four-arm data
  (`datasets/timeout_recovery/runs/`, seeds 42, 7, 2026) the per-case SD of paired ΔSR is at
  most 0.302 (`distilled_packet`, seed 42; 0.199–0.302 over the arms with non-zero
  variance). Planning value **σ_t = 0.30**, so `σ_t / √50 = 0.042`.
- `σ_b` is the between-run SD of the selection effect (which packets a run's selection
  finds). It has not been measured. Plateau SR in the committed data ranges from about 0.28
  (`no_sharing`) to about 0.91 (`distilled_packet`); a run-level outcome spread uniformly
  over that range has SD 0.63 / √12 ≈ 0.18. Planning value **σ_b = 0.20**, giving a
  per-run SD of **σ = 0.204**.

**Test.** Two-sided 95% CI (α = 0.05, 0.025 per side), effect of interest ΔSR = δ = 0.10.
Normal approximation, `SE = σ / √R` for R runs.

| Design | R (runs) | σ_b = 0.15 | σ_b = 0.20 (plan) | σ_b = 0.25 |
| --- | --- | --- | --- | --- |
| Draft: 4 seeds × 1 run × 200 tasks | 4 | 0.25 | 0.17 | 0.12 |
| 4 seeds × 8 runs | 32 | 0.95 | 0.79 | 0.61 |
| **Registered: 4 seeds × 12 runs** | **48** | **0.99** | **0.92** | **0.78** |

Power to declare R1 (CI lower bound > 0) when the true D1 − C is +0.10. The draft's design
is far too small, so N is raised before registration.

Power to declare R2 (CI inside ±0.10) when the true D1 − C is 0, with R = 48: 0.99, 0.85
and 0.56 for σ_b = 0.15, 0.20, 0.25. The required R at σ_b = 0.20 is 33 for R1 and 44 for
R2 at 80% power; 48 covers both.

**Registered sample: seeds 42, 7, 2026, 31337 × 12 runs (run index 0–11) × 200 tasks = 48
runs, 9,600 tasks per arm and host.** Run seeds are `stable_int("selection", seed, run)`.
The planning values are not re-estimated from the data and N is not changed after the
pilot.

## 10. Margin and noise floor

**δ = 0.10 ΔSR, one value used everywhere in this experiment**: the R1 grade (§12), the
equivalence bound of R2, the V2 falsification band (§11.2), the plateau tolerance (§8.3)
and the power target (§9). No other margin appears in this file.

The noise floor it must clear:

- At WP0–WP3's N = 30 cases, the 95% CI half-width of a paired ΔSR with non-zero variance is
  0.053–0.106 in the committed bootstrap CIs (`experiments/timeout_recovery/RESULTS.md`)
  and 0.054–0.108 by normal approximation from the per-case data. `docs/WP3_FINDINGS.md`
  gives about 0.08; the draft gave 0.08–0.13.
- At this experiment's registered size (R = 48 runs), the planned 95% half-width of the
  primary contrast is **0.058** at σ_b = 0.20, and 0.044–0.072 over σ_b = 0.15–0.25.

δ = 0.10 is above the planned noise floor of this design at every σ_b in §9, and above the
WP3 figure of 0.08. It is below the largest N = 30 half-width (0.106–0.108) and the
draft's upper estimate (0.13); those describe a 30-case sample, which this design does not
use. A realised CI that is wider than ±δ cannot satisfy R2 by construction, so an
underpowered run cannot be read as equivalence. If the realised half-width of the primary
contrast exceeds 0.072, the report says that the run was noisier than planned.

The WP3 margin (`WP3_R2_EQ_MARGIN = 0.05`) is not changed and does not apply here. The
expiry and budget experiments register their own margins (§15.2).

## 11. Validity checks

Both checks are computed for the primary contrast on `naive_host`, with the same paired
bootstrap as §12.

### 11.1 V1: content-stub sanity check (pipeline is not degenerate)

In arm V1 the strategy model of the C arm is replaced by a stub whose `derive()` returns
`{}`: packets are delivered, gated and attributed as in C, but carry no usable parameters.

- **Pass**: the CI of `D1 − V1` has lower bound > 0.
- **Fail**: otherwise. The pipeline does not turn absorbed content into SR, so a
  comparison of selection rules is meaningless. The result is **VOID**.

This is the draft's stub check. It tests the content path, not the envelope.

### 11.2 V2: envelope-shuffle falsification (gain comes from the envelope)

In arm V2, per run, one seeded permutation π of the 50 packets (`("shuffle", seed, run)`)
is drawn, and packet *p* receives the envelope fields `prior_outcomes`, `provenance` and
`verification_claim` of packet π(*p*). `id`, `type`, `absorption` and **`content` are
unchanged**. Because `provenance.source_ref` moves with the shuffle, the receiver's own
Absorption Results are attributed to shuffled sources, so attribution is ablated as well.
`history_selection` then runs exactly as in D1. π is a permutation, so the marginal
distribution of envelope values is unchanged; only their link to content is broken.

Under this ablation D must collapse to C:

- **Pass**: the 95% CI of `V2 − C` contains 0 and lies inside [−δ, +δ] = [−0.10, +0.10].
- **VOID**: the CI of `V2 − C` has lower bound > 0. Shuffled envelopes still beat random
  selection, so a D1 gain would not be attributable to the envelope.
- **Inconclusive**: anything else (CI wider than ±δ, or entirely below 0). The headline
  verdict is capped at `undetermined`.

## 12. Analysis and decision rules

**Estimate.** For each run, `Δ = plateau_SR(D1) − plateau_SR(C)` on `naive_host`. The
estimate is the mean over the 48 runs. **CI**: paired percentile bootstrap over runs,
stratified by seed (runs resampled within each of the four seeds), B = 2000, 95%, seeded
with `stable_int("selection-bootstrap", host, contrast)`. Per-seed means are reported
alongside, without a per-seed verdict.

Rules are applied in this order. The first that matches is the verdict.

| Rule | Condition | Verdict label |
| --- | --- | --- |
| V | V1 fails, or V2 is VOID | `VOID` (the reason is stated) |
| R1a | CI lower bound > 0 and point estimate ≥ δ | `supported: envelope-informed selection improves plateau SR by at least the margin` |
| R1b | CI lower bound > 0 and point estimate < δ | `supported: positive, below the 0.10 margin` |
| R3 | CI upper bound < 0 | `envelope-informed selection is worse` |
| R2 | CI inside [−δ, +δ] | `equivalent within ±0.10 ΔSR` |
| R4 | otherwise | `undetermined` |

If V2 is inconclusive (§11.2), R1a, R1b, R3 and R2 are reported as information and the
headline verdict is `undetermined`.

The same table is computed for each secondary contrast and labelled secondary. No headline
statement may rest on a secondary contrast.

## 13. Procedure

1. Merge this file (maintainer). Rules frozen.
2. Implement `experiments/selection/` as a new module that reuses `Host`, `packets.py`,
   `summarize_arm` and `bootstrap_ci` without modifying `experiments/timeout_recovery/`.
   The implementation PR states the commit of this file it implements. If a rule here
   cannot be implemented as written, an amendment PR to this file is merged **before any
   run**; the amendment is listed at the top of the results.
3. Pilot: seed 42, run indices 100–101, 50 tasks, to check that the pipeline runs and that
   V1/V2 compute. Pilot runs are not part of the analysis, and their numbers are not used
   to change any rule, N, δ or planning value. A bug found in the pilot is fixed in code
   and recorded in the results.
4. Main run: the 48 runs of §9 on both hosts, all arms of §4.1. Output to `/tmp` unless
   committing data with explicit flags, following the existing runner convention.
5. Write `experiments/selection/SELECTION_RESULTS.md` from the runner (generated, not
   hand-edited), then update the limitations in `docs/WP3_FINDINGS.md` or add
   `docs/PROTOCOL_LAYER_VALUE.md`.

## 14. Reporting and wording

| Term | Meaning | Not allowed |
| --- | --- | --- |
| `plateau_SR` | Mean task SR over T151–T200 | "capability" on its own |
| `D1 − C` | The increment from using envelope fields to select packets | "the protocol has no effect" when the verdict is R2 |
| `equivalent within ±0.10 ΔSR` | R2 | "no measurable increment", "no effect" |
| `undetermined` | Underpowered, or CI crosses the margin, or V2 inconclusive | "no increment" |
| attribution rate | Share of policy changes traceable to packet id + Absorption Result | "accountable" as a capability claim |

Every result states: the verdict label; the point estimate and CI of D1 − C; V1 and V2
outcomes; harmful (dishonest-peer) absorption per arm; the exchangeability gate status of
§15.1; and that the scenario is one simulated tool (`web_search` timeout recovery) with a
rule-parser receiver.

## 15. Order of work

### 15.1 In parallel: a minimal independent second implementation

The exchangeability gate (§3) needs an implementation that is independent of this
project's code and authorship. `second_impl/` is written from the SPEC without importing
the reference and passes `tests/conformance/`, but it is in this repository, in the same
language (Python), by the same maintainers, and it never absorbs, so it does not read the
envelope fields this experiment depends on. It does not satisfy the gate.

Built in parallel with experiment 1, in its own PR:

- A minimal receiver **in a language other than Python** (or, if that is not possible, an
  independent receiver written without access to `ai_oriented_a2a/` and `second_impl/`),
  from `SPEC.md`, `schema/`, `docs/TRANSPORT.md` and `docs/DATA_POLICY.md` only.
- **Gate**: it passes `tests/conformance/` in external mode (`A2A_PEER_CMD` or
  `A2A_PEER_URL`, see [`docs/COMPLIANCE.md`](COMPLIANCE.md)).
- **Envelope check**: given a frozen fixture of pools from this experiment, it parses
  `prior_outcomes`, `provenance` and `verification_claim` and outputs the same
  `history_selection` and `claim_selection` order as the Python runner.

Selection results do not wait for this gate. Until it passes, `SELECTION_RESULTS.md` must
say that the envelope is a single-project format, not yet an interoperable protocol.

### 15.2 Next: the expiry experiment, then the budget experiment

1. **Experiment 2, expiry (next).** A tool-behaviour change mid-sequence (a simulated API
   change) makes old experience stale. Arms: machine-checkable `expiry` used / not used.
   Primary endpoint: tasks needed to recover the pre-shift SR. Secondary: uses of stale
   experience, extra tool calls during recovery. It runs right after experiment 1, because
   recovering from stale experience after tool or API changes is the larger long-term risk.
2. **Experiment 3, budget (after expiry).** Whether `verification_claim.repro` lets a host
   skip unnecessary isolation. Primary endpoint: relative token reduction to reach the same
   plateau SR; harmful absorption must be reported alongside.

Each has its own pre-registration file (`docs/PREREG_EXPIRY.md`, then
`docs/PREREG_BUDGET.md`), its own margin, and its own merge before any run.

## 16. Relation to existing artifacts, and other decisions

- `experiments/timeout_recovery/run.py` R1–R4 (WP3) and their tests are **frozen**. This
  experiment adds no rule to them and changes none of their constants.
- `datasets/`: peer content reuses the synthetic `recovery_policy` format. Selection results
  go to `datasets/` only if byte-reproducible.
- `docs/LLM_RECEIVER_OBSERVATION.md`: an LLM-host run of this experiment is possible later;
  it is not part of this registration.

Decisions made here that neither the draft nor the review fixed, listed so a reviewer can
check them:

- Unit of analysis = run; N raised to 48 runs (§9).
- R1 split into R1a/R1b; a "worse" verdict (R3) added; rule order fixed (§12). The draft's
  "ΔSR ≥ 0.10 and CI lower > 0" has at most 50% power when the true effect is exactly
  0.10, so the margin grades the result instead of gating it.
- The harmful peer of the draft is the dishonest peer; the peer mix stays 2 effective, 2
  mediocre, 1 harmful (§6.1).
- Absorption slots every 15 tasks in T1–T150, plateau window T151–T200 (§5).
- `naive_host` is the primary host; `working_host` is secondary (§4.2).
- Known risk: with 20 effective packets in 50, random selection finds at least one
  effective packet in 10 draws in all but about 0.3% of runs, so `plateau_SR` may saturate
  in both arms and land in R2. If so, the difference is in `tasks_to_plateau`,
  `tokens_to_plateau` and AUC, which are reported but do not change the verdict.
