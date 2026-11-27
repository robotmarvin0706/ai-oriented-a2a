# Pre-registration: `verification_claim.repro` fast path and the verification budget (experiment 3)

> **Status: pre-registration. Non-normative.** It changes no wire behaviour, no schema, no
> `SPEC.md` text, no rule in `experiments/timeout_recovery/run.py`, and nothing in
> `experiments/selection/`, `experiments/expiry/`,
> [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md) or
> [`docs/PREREG_EXPIRY.md`](PREREG_EXPIRY.md).
>
> **No runs before merge.** No run of this experiment, including the pilot in §13, happens
> before the maintainer (`robotmarvin0706`) merges this file into `main`. The rules
> below are frozen at that merge. They are not edited after any data has been seen.
>
> Background: [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md) §15.2,
> [`experiments/selection/SELECTION_RESULTS.md`](../experiments/selection/SELECTION_RESULTS.md),
> [`experiments/expiry/EXPIRY_RESULTS.md`](../experiments/expiry/EXPIRY_RESULTS.md),
> [`docs/PROTOCOL_LAYER_VALUE.md`](PROTOCOL_LAYER_VALUE.md).

## 1. Why this experiment

Experiment 1 measured whether envelope fields help a receiver decide **which** packets to
try. Experiment 2 measured whether an envelope field helps it decide **when to stop using**
a packet. This experiment measures whether an envelope field lets a receiver **spend less
on verification** without giving up success rate or safety.

The setting is the one named in [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md) §15.2:

> Whether `verification_claim.repro` lets a host skip unnecessary isolation. Primary
> endpoint: relative token reduction to reach the same plateau SR; harmful absorption must
> be reported alongside.

The question: **when a receiver evaluates a stream of 40 candidate packets whose
`verification_claim` is honest for some packets, mismatched for others and forged by a
dishonest peer for others, does a receiver that uses `verification_claim.repro` to
fast-reject or spot-check eligible packets spend materially fewer total tokens than a
receiver that runs the full isolation gate on every candidate, with non-inferior plateau SR
and without exceeding a pre-registered harmful-absorption cap?**

Content, parser, gate, host, packet stream and task draws are the same in every arm. Only
the use of `verification_claim` differs.

## 2. Requirements and where they are applied

This file implements the requirements brief for experiment 3 of 2026-11-27.

| Requirement | Where it is applied |
| --- | --- |
| Docs-only; reuse the v0.4 `verification_claim` fields (`expected_effect`, `replay_hint`, `scenario_ref`, `metric`, `value`, `repro: {seed, model, n}`); no schema change | §5.3, §6.1 |
| Imperfect / adversarial `repro` stream with pre-registered rates: honest matching effective, mismatched (`model`, `scenario_ref`, undersized `n`), spoofed matching harmful, mediocre / no-`repro` | §5.2, §5.3 |
| Isolation cost is a material share of the measured budget; total run tokens and verification tokens stated | §7.4, §9.2 (verification tokens are 36% of C's total run tokens) |
| `naive_host` primary; why `working_host` is omitted | §4.2 |
| `plateau_SR` does not saturate in C; the token denominator is stable and non-degenerate | §9.5 |
| Arms A, C, D (D1 `repro_blind_skip`, D2 `repro_guarded_fast_path`), one designated primary | §4.1, §6.6 (D2 primary, D1 secondary) |
| Every token of parsing, eligibility checks, spot-checks and rollbacks accounted | §7.4 |
| V1 `content_stub` (`C − V1` on `plateau_SR` > 0, else VOID); V2 at D's exact per-run fast-path counts, uncoupled from `repro`, with Pass / VOID / Inconclusive rules | §7.3, §11 |
| Joint primary verdict: relative token reduction with `δ_tok`; `plateau_SR` non-inferiority with `δ_SR`; mandatory safety co-endpoint with a cap | §8.1, §8.2, §10, §12 |
| Secondary report-only endpoints | §8.3 |
| Unit = run; 4 seeds; seed-stratified paired percentile bootstrap, B = 2000; analytical variance model with sensitivity bounds; power ≥ 0.80 for R1, R2 and non-inferiority; N, seeds and the noisier-than-planned flag fixed before merge | §9, §10, §12 |
| Rule order `V > R1a > R1b > R3 > R2 > R4` adapted to the joint criterion; V2 inconclusive caps the headline at `undetermined` | §12 |
| Wording rules and caveats, including `impls/go/` and `repro` | §14, §15 |
| List every author-chosen parameter | §16 |
| `experiments/timeout_recovery/` R1–R4 stay frozen | §15 |

## 3. Layers

| Layer | Claim | Form | Status at registration |
| --- | --- | --- | --- |
| Precondition: exchangeable | Two independent implementations interoperate | Boolean (see §15) | Cross-language part met for the transport and the selection envelope (`impls/go/`); the fast-path rule of §6 is **not** implemented by any second implementation |
| Precondition: verifiable | Absorption has evidence and can be rolled back | Boolean | Met; fast-path decisions and probation rollbacks are logged too (§6) |
| Precondition: attributable | Every policy change traces to a packet | Boolean | Met; fast-path accepts, fast rejects and probation rollbacks are attributed to the packet id (§6) |
| **Target: verification cost** | Using `verification_claim.repro` reduces total run tokens with non-inferior SR and bounded harm | **Relative token reduction, jointly with `plateau_SR` non-inferiority and a harm cap** | This experiment |

Preconditions are entry gates, not findings, and are reported separately from the verdict.

## 4. Arms

### 4.1 Mapping table

| Label | Arm name in code and results | Packets delivered | `verification_claim` delivered | Use of `verification_claim` | Strategy model | Isolates |
| --- | --- | --- | --- | --- | --- | --- |
| A | `no_sharing` | none | — | — | — | baseline SR and baseline tokens |
| C | `always_full_gate` | the run's 40-packet stream (§5.2), over the protocol path | yes, ignored | none: full gate on every candidate | `deterministic-rule-host-v1` | cost of always verifying |
| D2 | `repro_guarded_fast_path` | same stream, same bytes | yes | eligibility check, fast reject, spot-checked fast accept with probation (§6) | same | **value of the `repro` fast path (D2 vs C), primary** |
| D1 | `repro_blind_skip` | same stream, same bytes | yes | eligibility check, fast reject, fast accept **without** spot-check or probation (§6.5) | same | trust risk of blind skipping (D1 vs C), secondary |
| V1 | `content_stub` | same stream | yes, ignored | none (as C) | stub, `derive() -> {}` | content-stub sanity check (§11.1) |
| V2 | `random_skip` | same stream, same bytes | yes, read but not used for routing | D2's guarded routes applied to **random** candidates, at D2's exact per-run counts (§7.3) | same | random-skip falsification (§11.2) |

- **Primary contrast: D2 vs C.** It is the only contrast that can produce the headline
  verdict (§12).
- Secondary contrasts, reported with the same rules but not used for the headline: D1 vs C;
  D2 vs V2; C vs A (tokens and SR); every contrast split by stratum (dishonest peer present
  / absent; effective packet present / absent, §5.2).
- Why D2 and not D1 is primary, and why D2's spot-check has 4 episodes, is computed in §6.6.

### 4.2 Host

All arms run on `naive_host` from `experiments/timeout_recovery/host.py`, unchanged:
starting policy `backoff=none,max_attempts=3,drop_stale=False`. `working_host` is not used
(§16): its locally configured policy is `PEER_POLICY` itself, the best policy the stream
can contain (§9.1), so no candidate can raise its SR and an honest claim never beats its
reference SR by the fast-accept margin of §6.3. Every eligible candidate would then be
fast-rejected by construction, and the experiment would measure only the cost of reading
claims, not the trade-off between verification cost and trust risk.

## 5. Environment

One **run** is one receiver working through one task sequence with one candidate stream.
All arms in a run share the run's task draws, stream, arrival order and gate draws (paired
design).

### 5.1 Tasks, arrivals and windows

- **Tasks.** 100 tasks, T1…T100. Task *t* of a run is `Case(run_seed, t)`, as in
  `experiments/selection/` and `experiments/expiry/`, with 3 `immediate` and 3 `delayed`
  evaluation episodes. Task SR is the mean success over its 6 episodes. The installed
  policy carries over between tasks.
- **Run length.** 100 tasks, not 200 as in experiments 1 and 2. Reason (§9.2): with 40
  candidates, 100 tasks make verification tokens 36% of C's total run tokens; with 200
  tasks the share would fall to about 20% and the primary endpoint would be dominated by
  evaluation tokens.
- **Arrivals.** Candidate *i* (i = 1…40) of the run's stream arrives at the start of task
  `T(10 + i)`, so candidates arrive at T11…T50, one per task. T1…T10 give every receiver
  60 task episodes of its own history before the first candidate (§6.2). Each candidate is
  evaluated once per arm; there is no retry and no queue.
- **Plateau window.** `plateau_SR` is the mean task SR over **T61–T100** (40 tasks). No
  candidate arrives and no probation (§6.4) is open in this window: the last arrival is T50
  and the last probation check is at T52.
- **Gate.** Every full-gate attempt goes through the unchanged host gate: content policy
  (SPEC §2.1), isolation (`ISOLATION_N = 3` trials, `DSR_THRESHOLD = 0.05` against the
  **current** policy, no regressed trial, `TOTAL_TOKEN_BUDGET = 3000`), single-step
  rollback, the §3.1 hard gate. The gate at task *t* uses the isolation draws of
  `Case(run_seed, t)`. Gate constants are those in `host.py` at the merge commit of this
  file.

### 5.2 Candidate stream (the imperfect `repro` signal)

Each run's stream has exactly **40 candidates**. Two per-run indicators, drawn from the
stream `("budget-stream", seed, run)`:

- `K_E ~ Bernoulli(q_E = 0.50)`: whether the stream contains the one effective packet.
- `K_S ~ Bernoulli(q_S = 0.50)`: whether the dishonest peer is present.

| Class | Count per run | Source (`provenance.source_ref`) | Content family | `verification_claim` |
| --- | --- | --- | --- | --- |
| **E**: honest, matching `repro`, effective | `K_E` (0 or 1) | `peer:peer-eff-1` | effective: `PEER_POLICY` (exponential, 400 ms, 4 attempts, drop stale) | honest, matching (§5.3) |
| **Mm**: honest, matching `repro`, mediocre | `18 − 4·K_S` (14 or 18) | `peer:peer-med-1`, `peer:peer-med-2` (alternating) | mediocre (exponential, 50 or 100 ms, 2 or 3 attempts, keep stale) | honest, matching |
| **X**: mismatched `repro` | 10 | `peer:peer-other-1` | 5 harmful, 5 mediocre (split below) | claimed elsewhere; `value ~ Uniform[0.85, 0.95]`; does not match this host (§5.3) |
| **S**: spoofed matching `repro`, harmful | `4·K_S` (0 or 4) | `peer:peer-dis-1` | harmful (`wrong_packet` family: `backoff` = none, `max_attempts` ∈ {6, "unbounded"}, keep stale) | forged, matching (§5.3) |
| **M0**: mediocre, no `repro` | `12 − K_E` (11 or 12) | `peer:peer-med-1`, `peer:peer-med-2` | mediocre | 6 carry a `verification_claim` with `expected_effect` only; the rest carry none |

Mismatch types inside class X (fixed per run): wrong `repro.model` 4 (2 harmful, 2
mediocre), wrong `scenario_ref` 3 (2 harmful, 1 mediocre), undersized `repro.n` 3 (1
harmful, 2 mediocre).

Pre-registered rates, per run: effective packet in 50% of runs; dishonest peer (4 spoofed
packets, 10% of the stream) in 50% of runs; mismatched claims 25% of the stream; honest
matching mediocre 35–45%; no-`repro` mediocre 27.5–30%. Family parameters and spoofed
values are drawn from `("budget-stream", seed, run)`; the arrival order is one uniform
permutation of the 40 candidates drawn from `("budget-order", seed, run)` and shared by all
arms. No arm sees a packet's class or family.

Every stream packet must pass `content_policy` (SPEC §2.1) and validate against
[`schema/experience-packet.schema.json`](../schema/experience-packet.schema.json). A
rejection of either kind is a generator bug: the run is discarded and fixed before the main
run, not counted.

### 5.3 Packets and claims

Every packet is a v0.4 Experience Packet with `type = "failure_mode"`,
`absorption = {"method": "prompt_injection", "target": "tool_retry_policy"}`, the
`recovery_policy` content format of `experiments/timeout_recovery/packets.py`, an opaque id
`pkt-bud-<16 hex>` that reveals neither source nor family, and `provenance.source_ref` as
in §5.2. No `prior_outcomes` and no `expiry` are sent (they are not studied here).

The host's scenario id is `HOST_SCENARIO_REF = "timeout_recovery/sim-v1/naive_host"`.
`verification_claim` uses only the existing v0.4 fields (`additionalProperties: false` on
both `verification_claim` and `repro`; nothing is added):

| Class | `scenario_ref` | `metric` | `value` | `repro` | `expected_effect` |
| --- | --- | --- | --- | --- | --- |
| E, Mm (honest, matching) | `HOST_SCENARIO_REF` | `"recovery_sr"` | mean task SR of the packet's policy over 30 fresh `naive_host` cases from the independent stream `("budget-prior", seed, run, packet_id, j)`, j = 0…29, rounded to 4 decimals | `{"seed": run_seed, "model": "deterministic-rule-host-v1", "n": 30}` | short text |
| X, wrong model | `HOST_SCENARIO_REF` | `"recovery_sr"` | `Uniform[0.85, 0.95]` | `{"seed": …, "model": "llm-receiver-v0", "n": 30}` | short text |
| X, wrong scenario | `"timeout_recovery/sim-v1/working_host"` | `"recovery_sr"` | `Uniform[0.85, 0.95]` | `{"seed": …, "model": "deterministic-rule-host-v1", "n": 30}` | short text |
| X, undersized n | `HOST_SCENARIO_REF` | `"recovery_sr"` | `Uniform[0.85, 0.95]` | `{"seed": …, "model": "deterministic-rule-host-v1", "n": 5 or 10}` | short text |
| S (forged) | `HOST_SCENARIO_REF` | `"recovery_sr"` | `Uniform[0.85, 0.95]`; nothing was run | `{"seed": …, "model": "deterministic-rule-host-v1", "n": 100}` | short text |
| M0 | — | — | — | — | 6 packets: `expected_effect` only; others: no `verification_claim` |

`replay_hint` is not sent. The prior-stream cases are never used for any scored task. The
forged claims are indistinguishable from honest matching claims by their fields alone;
`peer:peer-dis-1` does not impersonate another source.

## 6. The `repro` fast path

### 6.1 Claim parsing and eligibility (D1, D2)

For each candidate, D1 and D2 read the packet's `verification_claim` (and nothing else of
the envelope). The candidate is **eligible** for a fast path iff all of:

1. `verification_claim` is an object;
2. `metric == "recovery_sr"`;
3. `scenario_ref == HOST_SCENARIO_REF`;
4. `value` is a number (not a bool) with 0 ≤ `value` ≤ 1;
5. `repro` is an object; `repro.model == "deterministic-rule-host-v1"` (the host's own
   `MODEL_NAME`); `repro.n` is an integer (not a bool, not a float) with `n >= 30`;
   `repro.seed` is an integer (not a bool);
6. (D2 only) the packet's `provenance.source_ref` is not flagged in this run (§6.4).

Otherwise the candidate is **ineligible** and goes through the full gate, with a logged
reason `repro_ineligible:<first failed check>` (`no_claim`, `metric`, `scenario`, `value`,
`no_repro`, `model`, `n`, `seed`, `flagged_source`). The stream of §5.2 produces no
malformed claim; malformed inputs are covered by unit tests (§13).

`repro.seed` is parsed but **not** used to choose any draw: a spot-check that replays a
claimant-chosen seed would let a dishonest peer pick a seed on which its packet succeeds.

### 6.2 Reference SR

The reference `r(t)` at the start of task *t* is the host's own observed success rate:

- if at least 12 task episodes have run under the currently installed policy since its last
  change, `r(t)` is the mean success over the most recent `min(60, k)` of those `k`
  episodes;
- otherwise `r(t)` is **undefined** for D1 and D2, and an eligible candidate goes through
  the full gate (`repro_no_reference`).

The reference is local bookkeeping over outcomes the host has already observed; it is
charged no tokens. (V2's fallback for an undefined reference is in §7.3.)

### 6.3 Routes

For an eligible candidate with claimed value `v` and a defined reference `r = r(t)`:

| Condition | Route | What happens |
| --- | --- | --- |
| `v < r + DSR_THRESHOLD` (0.05) | **fast reject** | No isolation, no content read. Absorption Result `rejected`, reason `repro_claim_below_reference`. The claim itself says the gate would not promote the packet. |
| `v >= r + ACCEPT_MARGIN` (0.20) | **fast accept** | D2: spot-check (§6.4); D1: install at once (§6.5) |
| otherwise | full gate | as C |

### 6.4 D2: spot-check, escalation, probation and source flags

- **Spot-check.** The host derives the candidate's parameters from the wrapped content
  (absorb block, as in the gate) and runs **`SPOT_CHECK_N = 4`** episodes of the candidate
  policy in the window `"spot_check"` of `Case(run_seed, t)` (draws independent of the
  evaluation, isolation and rollback draws). The spot-check obeys the same
  `TOTAL_TOKEN_BUDGET` rule as the gate (absorb block + spot-check episodes ≤ 3000).
- **Pass** (all 4 episodes succeed): install the policy (single-step snapshot kept),
  Absorption Result `absorbed` with metrics `spot_check_n = 4`, `spot_check_successes = 4`,
  and open a **probation** for the packet.
- **Fail** (any episode fails, or budget exceeded): flag the packet's source and
  **escalate** to the unchanged full gate in the same task. The full gate's outcome is
  final.
- **Probation.** At the start of task `t + 2` the host compares the observed SR over the 12
  task episodes of tasks *t* and *t + 1* with the threshold
  `θ = max(r + DSR_THRESHOLD, v − PROBATION_TOL)`, `PROBATION_TOL = 0.30`. If the observed
  SR is below `θ`, it reverts to the snapshot in one step, replays 3 isolation-draw episodes
  on the restored policy (as the gate's post-rollback check), records a local result
  `rolled_back` (reason `probation_claim_not_reproduced`) attributed to the packet id, and
  flags the source. Probation costs no extra tool episodes: it reuses task episodes the
  host runs anyway. If another change is absorbed during probation, the probation ends
  without a verdict (`probation_superseded`).
- **Source flags.** A flagged source's later packets are ineligible for the rest of the run
  (§6.1 check 6).

### 6.5 D1: blind skip

D1 uses §6.1 checks 1–5, §6.2 and §6.3, and on **fast accept** installs the policy at once:
absorb block only, no spot-check, no probation, no source flags.

### 6.6 Which fast-path design gives a non-tautological test (planning)

Two analytical facts of `host.py` on `naive_host` shape the design (§9.1):

1. A harmful (`wrong_packet` family) policy has **exactly** the success of the naive policy
   on shared draws: its first three attempts are the naive attempts, and every later
   immediate retry is throttled (`THROTTLE_AFTER_IMMEDIATE = 3`) and cannot succeed. Its
   harm on `naive_host` is extra tool calls (+0.40 calls per episode for 6 attempts, +4.6 for
   "unbounded"), and, if it replaced an absorbed effective policy, an SR drop from 0.947 to
   0.340.
2. The full gate never absorbs a harmful packet over the naive policy (isolation ΔSR is
   exactly 0, below 0.05) or over `PEER_POLICY` (the harmful success set is a subset of
   the effective one on shared draws, so ΔSR ≤ 0). Over a mediocre policy, whose success is
   close to but not identical with naive, an absorption is possible but rare (committed
   data, experiment 1: 0 of 125 harmful attempts absorbed by C, 1 of 122 by V2). C's
   planned harmful absorption is therefore ≈ 0; it is measured, not assumed.

Consequences, computed with the planning model of §9.3:

- **D1 is not a fair primary.** Whenever the dishonest peer is present and a forged packet
  arrives while the host is still on a weak policy (`r ≤ v − 0.20`), D1 installs it with
  no check. Planned `harmful_absorption_rate(D1) = 0.443` (0.898 in runs with the dishonest
  peer), 16.9 tasks per run on a harmful policy, a persistent harmful policy at T61 in 18%
  of runs, and **more** total tokens than C (relative reduction −0.080). D1 would fail the
  safety cap of §10 by construction of the stream; it is kept as a secondary arm to show
  that trust risk.
- **D2's spot-check size.** Per spoofed attempt, a harmful packet passes an all-success
  spot-check of `k` episodes with probability `E[s_N(b)^k]`, an effective packet with
  `E[s_P1(b)^k]` (isolation window, no drift):

| `k` | harmful passes | effective passes | planned `harmful_absorption_rate(D2)` | planned relative token reduction |
| --- | --- | --- | --- | --- |
| 1 | 0.408 | 0.958 | 0.184 | 0.244 |
| 2 | 0.179 | 0.918 | 0.080 | 0.252 |
| 3 | 0.083 | 0.879 | 0.037 | 0.258 |
| **4 (registered)** | **0.040** | **0.843** | **0.017** | **0.259** |

  A 1-episode spot-check would pass 41% of forged harmful packets and breach the cap of
  §10; the token saving barely depends on `k`, because fast accepts are rare (1.06 per run)
  and the saving comes mostly from fast rejects. `SPOT_CHECK_N = 4` is registered.
- **Where the uncertainty lies for D2.** D2 can lose SR through spot-check failures
  followed by gate rejections, budget rejections and false probation rollbacks; it can
  absorb harmful packets that pass the spot-check (then exposed for up to 2 tasks); and it
  pays claim reads on every candidate, spot-check episodes, double absorb blocks on every
  escalation and rollback checks. Whether the net saving on **total** tokens clears
  `δ_tok`, with SR non-inferior and harm under the cap, is the test. V2 (§11.2) checks
  that the saving and the preserved SR come from `repro` choosing the right packets.

## 7. Arm behaviour

At the start of each task *t*, every arm executes these steps in order; A does only step 3.

1. **Probation check** (D2, V2): if a probation opened at `t − 2`, evaluate it (§6.4).
2. **Candidate** (only if 11 ≤ t ≤ 50): candidate `p = stream[t − 10]` arrives.
   - C: full gate.
   - V1: `host.absorb` with the stub model: `derive()` returns `{}`, so the packet is
     rejected before isolation (`no recovery parameters found`); only the absorb block is
     charged.
   - D1, D2: claim read (§7.4), eligibility (§6.1), reference (§6.2), route (§6.3–§6.5).
   - V2: claim read (§7.4), route from V2's plan (§7.3).
3. **Episodes**: run the task's 6 episodes with the installed policy.

### 7.1 What each arm does with each class (planning)

| Class | C | D2 while on the naive start policy | D2 after the effective packet is absorbed |
| --- | --- | --- | --- |
| E | full gate, accepted with a = 0.897 | fast accept; spot-check passes with 0.843, else escalation to the full gate (same draws as C) | — |
| Mm | full gate; accepted by noise with about 0.10 (mediocre SR ≈ naive) | fast reject when `v < r + 0.05`; full gate when `r` is low by noise (planned 11.7 fast rejects per run over all classes) | fast reject |
| X | full gate (never accepted if harmful) | full gate | full gate |
| S | full gate, never accepted on the naive policy or `PEER_POLICY` | fast accept → spot-check passes with 0.040 → probation rollback; source flagged after the first S packet either way | fast reject (`v ≤ 0.95 < r + 0.05` at `r ≈ 0.947`), or full gate when `r` is low by noise |
| M0 | full gate | full gate | full gate |

### 7.2 Paired draws

All arms use the same `Case(run_seed, t)` draws. A full gate at task *t* sees the same
isolation draws in every arm, so an escalated D2 gate on the same current policy reaches
the same decision as C's gate on that packet.

### 7.3 V2 plan

Let `n_fr` and `n_fa` be the numbers of fast rejects and fast accepts of **D2 in the same
run**. V2 draws, from `("budget-v2", seed, run)`, `n_fr + n_fa` distinct arrival positions
uniformly from the 40, assigns the first `n_fr` to fast reject and the next `n_fa` to fast
accept, and sends every other candidate through the full gate, regardless of its claim. So
V2 has D2's exact per-run fast-path counts, and only the coupling of the route to `repro`
is removed. At a V2 fast accept, D2's §6.4 steps apply (spot-check of 4, escalation on
failure, probation) with `r` = `r(t)` if defined, else the mean of the most recent 12 task
episodes, and `θ = max(r + 0.05, v − 0.30)`, where the `v` term is used only if the packet
has a numeric claimed `value` in [0, 1]. V2's routes are fixed by its plan; source flags do
not change them.

### 7.4 Token accounting

All token counts use `approx_tokens` of `host.py`. Per run and arm:

- **`task_tokens`**: the 600 evaluation episodes, each `tool_calls × CALL_TOKENS` plus the
  resident absorbed context, as in `host.py`.
- **`gate_tokens`**: for every full-gate attempt, the absorb block
  (`approx_tokens(wrap_untrusted(packet))`), both isolation windows and the post-rollback
  replay (as `experiments/selection/`); for V1, the absorb block only.
- **`repro_overhead_tokens`** (D1, D2, V2): for every candidate the claim read,
  `approx_tokens({"verification_claim": <claim or null>})` (71 tokens for an honest
  matching claim, 6 when absent); for every fast accept the absorb block; for every
  spot-check its 4 episodes; for every probation rollback its 3 replay episodes. Fast
  rejects are charged the claim read only. The reference SR (§6.2) is not charged.
- **`verification_tokens = gate_tokens + repro_overhead_tokens`**.
- **`total_tokens = task_tokens + verification_tokens`**, over T1–T100. This is the
  denominator of the primary endpoint. Nothing is excluded from it.

C is not charged claim reads: it ignores `verification_claim` (and `wrap_untrusted` puts
only `id`, `type` and `content` in context).

## 8. Endpoints

### 8.1 Primary endpoints (joint)

1. **Efficiency.** `rel_token_reduction(X vs C) = 1 − Σ_runs total_tokens(X) /
   Σ_runs total_tokens(C)` (ratio of paired means over the 160 runs). This one formula is
   used for every relative token figure in this experiment, primary and secondary, and in
   the bootstrap (runs resampled jointly for both arms).
2. **Quality non-inferiority.** Per run `Δ_SR = plateau_SR(X) − plateau_SR(C)`; estimate =
   mean over runs. Non-inferiority holds iff the 95% CI lower bound of `Δ_SR` is
   `> −δ_SR`.
3. **Safety co-endpoint.** `harmful_absorption_rate(X)` = share of runs in which the policy
   of a harmful packet (class S or harmful X) was installed for at least one scored task,
   by any route. The safety guardrail holds iff the 95% CI upper bound of
   `harmful_absorption_rate(X) − harmful_absorption_rate(C)` (paired per run) is
   `<= c_harm`.

The §15.2 wording "relative token reduction to reach the same plateau SR" is
operationalised as the relative reduction of total tokens over a fixed 100-task run,
jointly with `plateau_SR` non-inferiority. `tokens_to_plateau` of experiment 1 is not used:
it depends on plateau detection and would make the denominator itself a random stopping
time.

### 8.2 Safety figures reported next to every headline

Total harmful installations; harmful packets fast-accepted, spot-check passed, absorbed by
the full gate and rolled back (gate rollback or probation rollback); **persistent harmful
absorption** (a harmful policy installed at the start of T61); `tasks_on_harmful_policy`.

### 8.3 Secondary endpoints (reported, no verdict)

- `gate_tokens`, `repro_overhead_tokens` and `verification_tokens`, absolute and as
  relative reduction (§8.1 formula), and `task_tokens`;
- resident context tokens at T100 (packets absorbed and kept);
- `full_sequence_SR` (mean task SR over T1–T100);
- `fast_path_rate` = (fast rejects + fast accepts) / 40 per run, and each route separately;
- `false_fast_path_rate` = fast-path events on the wrong packet (a fast accept of a
  non-effective packet, or a fast reject of the effective packet) / fast-path events;
- spot-check passes and failures by family, escalations, probation rollbacks (true /
  false), `probation_superseded`, budget rejections;
- `rollbacks` (gate and probation) and `tasks_on_harmful_policy`;
- attribution rate (policy changes, fast rejects and probation rollbacks traceable to a
  packet id with a matching result). Process metric, not capability;
- every contrast split by stratum: dishonest peer present / absent (`K_S`), effective
  packet present / absent (`K_E`). Descriptive, no verdict.

## 9. Planning model, sample size and power

The unit of analysis is the **run**. Tasks in a run share one stream and one sequence of
verification decisions, so they are not independent replicates. No number in this section
comes from a run of this experiment or a pilot.

### 9.1 Per-episode quantities (closed form)

Let `b ~ Uniform(0.55, 0.85)` be a case's base timeout probability; isolation and
spot-check episodes use `b`, task SR averages `b` (immediate) and `b + 0.10` (delayed).
With the loop of `host.py` (formulas as in [`docs/PREREG_EXPIRY.md`](PREREG_EXPIRY.md) §6.3):

| Quantity | naive / harmful success | `PEER_POLICY` (effective) |
| --- | --- | --- |
| task SR | 0.340 (harmful: identical, §6.6) | 0.947 |
| isolation-window success `E[s(b)]` | 0.408 | 0.958 |
| `E[s(b)^4]` (spot-check pass) | 0.040 | 0.843 |
| tool calls per task episode | 2.055 (harmful: +0.40 for 6 attempts, +4.64 for "unbounded") | 2.268 |
| gate acceptance over naive | — | a = 0.897 |

Mediocre policies are treated as naive in success (committed data: +0.00 to +0.01 ΔSR) and
are accepted by the full gate over a naive or mediocre policy with probability **0.10**
(committed: 43 of 475 in experiment 1, 1–5 of 30 in WP3), never over `PEER_POLICY`.
Absorb blocks are 136 (effective), 129 (mediocre) and 135 (harmful) tokens; each absorbed
block stays in context for every later episode, as in `host.py`.

**Validation against committed data (not a run).** 2.055 calls per naive episode give
82.2 tokens per episode, matching `no_sharing` total tokens 98,652 over 200 tasks
(82.2 per episode) in
[`experiments/selection/SELECTION_RESULTS.md`](../experiments/selection/SELECTION_RESULTS.md);
0.340 and 0.947 match the committed naive and `PEER_POLICY` SRs; a = 0.897 matches 25–27
of 30 for `distilled_packet` in WP3.

### 9.2 Isolation cost is a material share of the budget

A full gate that rejects a mediocre packet on the naive policy costs about 1,220 tokens
(absorb 129 + before 3 × 79 + after 3 × 208 + replay 3 × 79), about 2.5 naive tasks
(493 tokens each); on `PEER_POLICY` with its block resident it costs about 2,490 tokens.
In experiment 1 a 200-task run had 4 gate attempts, so isolation was a few per cent of
total tokens; here 40 gate attempts face 100 tasks. Planned per-run means (§9.3):

| Arm | `total_tokens` | `task_tokens` | `verification_tokens` (gate + `repro` overhead) | verification share |
| --- | --- | --- | --- | --- |
| A | 49,316 | 49,316 | 0 | 0 |
| C | 338,766 | 216,551 | 122,215 (122,215 + 0) | **36.1%** |
| D2 | 250,876 | 173,598 | 77,278 (73,107 + 4,171) | 30.8% |
| D1 | 365,815 | 267,136 | 98,679 (95,934 + 2,745) | 27.0% |
| V1 | 54,521 | 49,316 | 5,205 (5,205 + 0) | 9.5% |
| V2 | 257,543 | 179,142 | 78,402 (74,119 + 4,283) | 30.4% |

Planned `rel_token_reduction(D2 vs C) = 0.259`. It has two parts, both reported (§8.3):

- **Verification tokens**: −36.8% (gate tokens −40.2%), i.e. 0.133 of C's total tokens;
  this part alone exceeds `δ_tok = 0.10`.
- **Task tokens**: −19.8%. C's 3-trial gate accepts mediocre packets by noise (0.10 each)
  and keeps their blocks in context for every later episode (resident context at T100:
  C 382, D2 283 tokens planned); D2's fast reject, based on a 30-case claim, avoids most of
  those absorptions. This is a real consequence of skipping isolation in `host.py`, not a
  change of denominator, and it is reported separately so that no claim rests on it alone.

With 200 tasks instead of 100 the verification share in C would be 19.6% (same model).

### 9.3 Variance model

The planning model is a Monte Carlo of the formulas above (20,000 simulated runs of the
closed-form success probabilities, gate acceptances, claim noise and token charges, with
common random numbers for paired episodes; **not** of the simulator). Claim noise: honest
`value` = true SR + N(0, 0.035) for mediocre and N(0, 0.018) for the effective packet (the
SD of a 30-case mean). Results:

| Contrast | Planned estimate | Run-level SD |
| --- | --- | --- |
| `rel_token_reduction(D2 vs C)` | 0.259 | 0.196 (delta-method SD of `(C_r − D_r) − ρ·C_r` over mean `C`) |
| `Δ_SR` = `plateau_SR(D2) − plateau_SR(C)` | +0.027 | 0.126 |
| `harmful_absorption_rate(D2) − (C)` | +0.017 | 0.129 |
| `plateau_SR(C) − plateau_SR(V1)` | +0.267 | 0.303 |
| `plateau_SR(V2) − plateau_SR(C)` | −0.084 | 0.218 |
| `rel_token_reduction(V2 vs C)` | 0.240 | 0.192 |

**Planning values: σ_tok = 0.20** (sensitivity **0.15 to 0.25**); **σ_SR = 0.13**
(sensitivity **0.10 to 0.17**); **`harmful_absorption_rate(D2)` = 0.017** (sensitivity up
to **0.05**). Sensitivity of the planning model to its own inputs (rows recomputed with one
input changed): mediocre gate acceptance 0.05 / 0.17 → relative reduction 0.218 / 0.259,
`plateau_SR(C)` 0.606 / 0.575, verification share 38.6% / 35.1%; `q_E` 0.40 / 0.60 →
relative reduction 0.261 / 0.257, `plateau_SR(C)` 0.558 / 0.662, V2 − C −0.067 / −0.102.

D2's planned `plateau_SR` (0.635) is above C's (0.607): when C's gate rejects the
effective packet (probability 0.103), D2's independent spot-check still accepts it with
0.843. This is part of the planning model, not assumed in any rule.

### 9.4 Power

Two-sided 95% CI (α = 0.05), normal approximation, `SE = σ / √R`.

- **R1** (token CI lower bound > 0 and point estimate ≥ `δ_tok`) when the true reduction
  is `δ_tok`: `Φ(δ_tok/SE − 1.96)`. **R2** (token CI inside ±`δ_tok`) when the true
  reduction is 0: `2Φ(δ_tok/SE − 1.96) − 1`.
- **Non-inferiority** (`Δ_SR` lower bound > −`δ_SR`) when the true `Δ_SR` is **0**
  (conservative; planned +0.027): `Φ(δ_SR/SE − 1.96)`.
- **Safety** (upper bound of the harm difference ≤ `c_harm`), exact binomial over runs with
  the normal-approximation bound, taking C's rate as 0 (§6.6; a small positive C rate only
  lowers the difference).

| R (runs) | R1 / R2 at σ_tok = 0.15 | 0.20 (plan) | 0.25 | NI at σ_SR = 0.10 | 0.13 (plan) | 0.17 | Safety at p = 0.017 (plan) | 0.034 | 0.05 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 80 (4 × 20) | 1.000 / 1.000 | 0.994 / 0.988 | 0.947 / 0.894 | 0.994 | 0.931 | 0.749 | 0.988 | 0.863 | 0.629 |
| 120 (4 × 30) | 1.000 / 1.000 | 1.000 / 1.000 | 0.992 / 0.985 | 1.000 | 0.988 | 0.897 | 0.995 | 0.884 | 0.606 |
| **160 (4 × 40, registered)** | **1.000 / 1.000** | **1.000 / 1.000** | **0.999 / 0.998** | **1.000** | **0.998** | **0.961** | **1.000** | **0.952** | **0.720** |

80% power needs `R >= (2.802 σ / δ)²` for R1 and `R >= (3.242 σ / δ)²` for R2: 31 and 42
runs at σ_tok = 0.20, 49 and 66 at 0.25; and 53 runs for non-inferiority at σ_SR = 0.13,
91 at 0.17. The registered 160 runs keep R1, R2 and non-inferiority above 0.96 over the
whole sensitivity range. Safety power is 0.95 up to a true D2 rate of 0.034 (the planned
rate if every run had the dishonest peer) and 0.72 at 0.05; this is reported, and the cap
is not changed. The validity checks (§11) have power ≥ 0.99 at their planned values
(V1: +0.267, SD 0.303; V2 Pass via (i): −0.084, SD 0.218, and 0.989 at −0.064, SD 0.19).
Planned power of the full R1a conjunction at the planning values: ≥ 0.99.

**Registered sample: seeds 42, 7, 2026, 31337 × 40 runs (run index 0–39) × 100 tasks = 160
runs, 16,000 tasks per arm.** Run seeds are `stable_int("budget", seed, run)`. Planning
values are not re-estimated from the data, and N is not changed after the pilot.

### 9.5 Non-saturation and a stable denominator

- `plateau_SR` in C is a mixture of runs that end on `PEER_POLICY` (0.947) and runs that
  stay naive or mediocre (0.340): planned mean **0.607**, per-run SD 0.303; by stratum
  0.878 (effective packet present) and 0.340 (absent). D2: 0.635. Neither arm can approach
  0 (the naive floor is 0.340) or 1 (the best policy in the stream is 0.947), and half of
  the runs contain no effective packet by design, so the mean cannot saturate. If a
  realised mean of C or D2 lies outside [0.10, 0.90], the report says that the endpoint
  saturated contrary to plan; the decision rules are not changed.
- The token denominator `Σ total_tokens(C)` is a sum over 160 runs of values that are
  never below about 97,000 (planned minimum: the 100 naive tasks plus 40 gates) and have a
  planned mean of 338,766 and SD 123,271 per run; the ratio estimator is not near a
  division by zero, and its SE (0.20/√160 ≈ 0.016) is small relative to `δ_tok`.

## 10. Margins and noise floor

| Margin | Value | Used for | Rationale |
| --- | --- | --- | --- |
| `δ_tok` | **0.10** relative reduction of total run tokens | R1a / R1b grade, R2 equivalence bound, V2 Pass (ii), power target | A tenth of the whole run budget; below it, adding a fast-path rule to a host is not worth its complexity. The verification part alone (0.133 planned) clears it. |
| `δ_SR` | **0.05** `plateau_SR` | non-inferiority of R1a, R1b, R2; R3 | Equal to the host's own `DSR_THRESHOLD`: a loss smaller than the minimum gain the gate itself treats as meaningful |
| `c_harm` | **0.10** difference in `harmful_absorption_rate` | safety guardrail of R1a, R1b, R2; R3; V2 Pass (iii) | At most one run in ten more than C with any harmful installation; persistent harm and `tasks_on_harmful_policy` are reported next to it |

No other margin appears in this file. These are chosen for this experiment, not carried
over from WP3 (`WP3_R2_EQ_MARGIN = 0.05`, unchanged), experiment 1 (δ = 0.10 on SR,
unchanged) or experiment 2 (δ = 0.08, unchanged).

- **Noise floor.** The planned 95% half-width of the primary token contrast at R = 160 is
  **0.023** at σ_tok = 0.15, **0.031** at the planning σ_tok = 0.20 and **0.039** at 0.25.
  `δ_tok` is strictly above all of them, by a factor of at least 2.5. The planned
  half-width of `Δ_SR` is 0.016 / 0.020 / 0.026 (σ_SR 0.10 / 0.13 / 0.17), below `δ_SR`.
- **Noisier-than-planned flag.** If the realised half-width of the primary
  `rel_token_reduction` exceeds **0.039**, the report says that the run was noisier than
  planned. The realised half-width of `Δ_SR` is reported against 0.026.
- A realised token CI wider than ±`δ_tok` cannot satisfy R2, so an underpowered run cannot
  be read as equivalence.

## 11. Validity checks

Both checks use the paired bootstrap of §12.

### 11.1 V1: content-stub sanity check

V1 is C with a strategy model whose `derive()` returns `{}`: packets are delivered and
attributed as in C, but nothing usable reaches the policy, so V1 stays naive.

- **Pass**: the 95% CI of `plateau_SR(C) − plateau_SR(V1)` has lower bound > 0. Planned:
  +0.267.
- **Fail**: otherwise. Absorbed content does not reach SR, so the experiment cannot tell a
  cheap verifier from one that never lets anything through. The result is **VOID**.

### 11.2 V2: random-skip falsification

V2 fast-paths D2's exact number of candidates per run, chosen at random (§7.3). It saves
roughly the same verification tokens as D2 by construction, so it tests whether `repro`
chooses the **right** packets to fast-path: random fast rejects hit the effective packet,
random fast accepts hit mediocre and harmful packets.

- **VOID**: V2 against C meets the full R1a condition of §12 (token reduction CI lower
  bound > 0 and point ≥ `δ_tok`, non-inferiority, safety guardrail). Then random skipping
  explains a result of the size the experiment would call support.
- **Pass**: not VOID, and V2 against C fails the joint criterion decisively on at least one
  component: (i) the 95% CI upper bound of `plateau_SR(V2) − plateau_SR(C)` is < 0; or
  (ii) the 95% CI upper bound of `rel_token_reduction(V2 vs C)` is < `δ_tok`; or (iii) the
  95% CI lower bound of `harmful_absorption_rate(V2) − (C)` is > `c_harm`. Planned: (i),
  −0.084.
- **Inconclusive**: anything else. R1a, R1b, R3 and R2 are then reported as information and
  the headline verdict is `undetermined`.

D2 vs V2 (tokens, `plateau_SR`, harm) is reported as a secondary contrast.

## 12. Analysis and decision rules

**Estimates and CIs.** The token contrast uses the §8.1 ratio of paired means; `Δ_SR` and
the harm difference are means over runs of paired per-run differences. **CI**: paired
percentile bootstrap over runs, stratified by seed (runs resampled within each of the four
seeds; the same resampled runs for both arms), B = 2000, 95%, seeded with
`stable_int("budget-bootstrap", contrast)`. Per-seed means are reported alongside, without a
per-seed verdict.

Define for the primary contrast D2 vs C:

- **TOK** = the 95% CI of `rel_token_reduction(D2 vs C)` and its point estimate;
- **NI** holds iff the 95% CI lower bound of `Δ_SR` is > −0.05;
- **SAFE** holds iff the 95% CI upper bound of the harm difference is ≤ 0.10.

Rules are applied in this order. The first that matches is the verdict.

| Rule | Condition | Verdict label |
| --- | --- | --- |
| V | V1 fails, or V2 is VOID | `VOID` (the reason is stated) |
| R1a | TOK lower bound > 0 and point estimate ≥ `δ_tok`, **and** NI **and** SAFE | `supported: the repro fast path reduces total run tokens by at least the margin, with non-inferior plateau SR and harm within the cap` |
| R1b | TOK lower bound > 0 and point estimate < `δ_tok`, **and** NI **and** SAFE | `positive but below the 0.10 token margin; not practically significant` |
| R3 | TOK upper bound < 0, **or** not NI, **or** not SAFE | `not supported: the fast path fails the joint criterion (<reasons>)`, with every applicable reason of `more_tokens`, `plateau_SR_not_non_inferior`, `harm_cap_breached` |
| R2 | TOK inside [−`δ_tok`, +`δ_tok`], and NI and SAFE | `equivalent within ±0.10 relative total tokens` |
| R4 | otherwise | `undetermined` |

**Headline.** Only R1a may be stated as support for the `repro` fast path. An R1b result is
reported with its label verbatim and must not be called "supported", in the headline or
elsewhere. An R3 result names its reasons; "worse" may be used only for the reasons that
apply.

If V2 is inconclusive (§11.2), R1a, R1b, R3 and R2 are reported as information and the
headline verdict is `undetermined`.

The same table is computed for each secondary contrast (D1 vs C; strata) and labelled
secondary. No headline statement may rest on a secondary contrast, on one stratum, or on
the verification-token or task-token parts of §9.2 alone.

## 13. Procedure

1. Merge this file (maintainer, `robotmarvin0706`). Rules frozen.
2. Implement `experiments/budget/` as a new module that reuses `Host`, `Case`, the strategy
   models, `approx_tokens`, `stable_int`, `wrap_untrusted` and the gate without modifying
   `experiments/timeout_recovery/`, `experiments/selection/` or `experiments/expiry/`; the
   stream generator, the claim parser, the reference SR, the spot-check, the probation and
   the V2 plan live in the new module. The implementation PR states the commit of this file
   it implements and includes unit tests for: schema validity of every generated packet;
   every `repro_ineligible` reason of §6.1 (including bools as integers, a float `n`,
   `value` outside [0, 1], a missing `repro`); the routes of §6.3 at their boundaries; the
   spot-check, escalation, probation and source flags of §6.4; that D1 never spot-checks;
   that V2's per-run counts equal D2's; and the token accounting of §7.4. If a rule here
   cannot be implemented as written, an amendment PR to this file is merged **before any
   run**; the amendment is listed at the top of the results.
3. Pilot: seed 42, run indices 100–101, 100 tasks, to check that the pipeline runs and that
   V1/V2 compute. Pilot runs are not part of the analysis, and their numbers are not used
   to change any rule, N, margin or planning value. A bug found in the pilot is fixed in
   code and recorded in the results.
4. Main run: the 160 runs of §9 on `naive_host`, all arms of §4.1. Output to `/tmp` unless
   committing data with explicit flags, following the existing runner convention.
5. Write `experiments/budget/BUDGET_RESULTS.md` from the runner (generated, not
   hand-edited), then update [`docs/PROTOCOL_LAYER_VALUE.md`](PROTOCOL_LAYER_VALUE.md).

## 14. Reporting and wording

| Term | Meaning | Not allowed |
| --- | --- | --- |
| `rel_token_reduction` | `1 − Σ total_tokens(X) / Σ total_tokens(C)` over T1–T100 | "cheaper" or "saves tokens" without `plateau_SR` and harm next to it; any other formula |
| fast path | fast reject or fast accept under §6 | "skips verification" for D2 (D2 spot-checks and monitors) |
| `harmful_absorption_rate` | Share of runs with a harmful policy installed for ≥ 1 scored task | "safe" or "no harm" when persistent harm or `tasks_on_harmful_policy` is non-zero without stating them |
| `equivalent within ±0.10 relative total tokens` | R2 | "no effect", "repro is useless" |
| `undetermined` | CI crosses the margin, or V2 inconclusive | "no increment", "no effect" |
| attribution rate | Share of policy changes, fast rejects and rollbacks traceable to packet id + result | "accountable" as a capability claim |

Every result states: the verdict label; the point estimate and CI of
`rel_token_reduction(D2 vs C)` **together with** the `Δ_SR` estimate and CI, and the
harmful-absorption difference with CI, persistent harmful absorption and
`tasks_on_harmful_policy` — **every token-reduction claim, in the headline or elsewhere,
is reported next to `plateau_SR` and harmful absorption**; V1 and V2 outcomes; the
half-width flag; the split into verification tokens and task tokens (§9.2); D1's result
as the trust-risk contrast; the pre-registered stream rates (effective packet in 50% of
runs, dishonest peer in 50% of runs, 25% mismatched claims); that the fast-path rule of §6
is a single-project, non-normative receiver rule over the v0.4 `verification_claim` fields;
that `impls/go/` reads `verification_claim.repro.n` only for the experiment 1
`claim_selection` ranking and does not use `verification_claim.repro` for gate skipping or
any absorption decision; and that the scenario is one simulated tool (`web_search` timeout
recovery) with a rule-parser receiver (`deterministic-rule-host-v1`), synthetic peers and
forged claims generated by the experiment, not an LLM receiver or a real API.

## 15. Relation to existing artifacts and order of work

- `experiments/timeout_recovery/run.py` R1–R4 (WP3) and their tests are **frozen**. This
  experiment adds no rule to them and changes none of their constants (`DSR_THRESHOLD`,
  `ISOLATION_N` and `TOTAL_TOKEN_BUDGET` are used as they are).
- `experiments/selection/`, `experiments/expiry/`,
  [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md) and
  [`docs/PREREG_EXPIRY.md`](PREREG_EXPIRY.md) are not changed. Their margins do not apply
  here.
- `schema/` and `SPEC.md` are not changed: the experiment uses only the existing optional
  v0.4 `verification_claim` fields.
- Exchangeability: `impls/go/` passes `tests/conformance/` in external mode and matches the
  selection ranking (see [`docs/COMPLIANCE.md`](COMPLIANCE.md)); it is maintained by the
  same maintainers and does not implement the fast path of §6. Until a second
  implementation does, results must call the fast path a single-project receiver rule, not
  an interoperable protocol feature. Extending `impls/go/` is not part of this
  registration.

## 16. Decisions for review

Decisions made here that the requirements brief did not fix, listed so a reviewer can check
them before merge:

1. **Primary arm** D2 `repro_guarded_fast_path`; D1 `repro_blind_skip` secondary, because
   D1 fails the harm cap by construction whenever the dishonest peer is present (§6.6).
2. **Fast-path routes**: fast reject at `v < r + 0.05` (`DSR_THRESHOLD`), fast accept at
   `v >= r + 0.20` (`ACCEPT_MARGIN`), full gate in between (§6.3).
3. **Reference SR**: the host's own mean success over the most recent `min(60, k)` episodes
   under the current policy, at least 12 required, not charged (§6.2).
4. **Spot-check**: `SPOT_CHECK_N = 4`, all must succeed, own draws in window
   `"spot_check"`, never the claimant's `repro.seed`; failure escalates to the full gate
   and flags the source (§6.4, §6.6).
5. **Probation**: 2 tasks (12 episodes), `θ = max(r + 0.05, v − 0.30)`
   (`PROBATION_TOL = 0.30`), rollback with a 3-episode replay, source flag (§6.4).
6. **Source flags** apply to D2 only (not D1, not V2's routes) (§6.4, §6.5, §7.3).
7. **Eligibility**: `metric == "recovery_sr"`, `scenario_ref == HOST_SCENARIO_REF =
   "timeout_recovery/sim-v1/naive_host"`, `value` in [0, 1], `repro.model ==
   "deterministic-rule-host-v1"`, integer `repro.n >= 30`, integer `repro.seed` (§6.1).
8. **Stream**: 40 candidates; `q_E = 0.50` (one `PEER_POLICY` packet), `q_S = 0.50` (4
   forged packets), 10 mismatched (4 model / 3 scenario / 3 undersized `n`; 5 harmful,
   5 mediocre), 14 or 18 honest matching mediocre, 11 or 12 no-`repro` mediocre (§5.2).
9. **Claims**: honest values from 30 independent prior-stream cases; forged and mismatched
   values `Uniform[0.85, 0.95]`; forged `n = 100`; wrong model `"llm-receiver-v0"`; wrong
   scenario `"timeout_recovery/sim-v1/working_host"`; undersized `n` ∈ {5, 10} (§5.3).
10. **Effective content** is exactly `PEER_POLICY` (not the 4-variant family of experiment
    1), so the planning values are validated by committed data (§9.1).
11. **Run length** 100 tasks, arrivals T11–T50, plateau window T61–T100 (40 tasks) (§5.1,
    §9.2).
12. **Host**: `naive_host` only; `working_host` dropped (§4.2).
13. **Token accounting**: claim reads charged to D1, D2 and V2 on every candidate (not to
    C); the reference SR is free; a fast accept is charged its absorb block, and an
    escalation charges the block again inside the full gate (§7.4).
14. **Primary token formula**: ratio of paired means `1 − Σ D / Σ C` over total tokens,
    used everywhere (§8.1).
15. **`harmful_absorption_rate`** counts any installation for ≥ 1 scored task, including
    ones later rolled back by probation (§8.1).
16. **Margins** `δ_tok = 0.10`, `δ_SR = 0.05`, `c_harm = 0.10` (§10).
17. **Noisier-than-planned flag** at a realised token half-width > 0.039 (§10).
18. **Planning values** σ_tok = 0.20 (0.15–0.25), σ_SR = 0.13 (0.10–0.17), D2 harm rate
    0.017 (up to 0.05); mediocre gate acceptance 0.10; claim-noise SDs 0.035 / 0.018; from
    the closed-form planning model of §9, not from any run of this experiment (§9.3).
19. **N = 160 runs** (seeds 42, 7, 2026, 31337 × run indices 0–39), 100 tasks; run seed
    `stable_int("budget", seed, run)` (§9.4).
20. **V2** = random skip at D2's exact per-run fast-reject and fast-accept counts, with D2's
    spot-check and probation at its fast accepts and a fallback reference (§7.3); Pass
    requires a decisive failure of one joint component (§11.2).
21. **R3** covers more tokens, failed non-inferiority and a breached harm cap, with reason
    codes (§12).
22. **Saturation report**: a realised C or D2 mean `plateau_SR` outside [0.10, 0.90] is
    reported as saturation contrary to plan, without changing the rules (§9.5).
23. **Random streams**: `("budget-stream", seed, run)`, `("budget-order", seed, run)`,
    `("budget-prior", seed, run, packet_id, j)`, `("budget-v2", seed, run)`, the episode
    window `"spot_check"`, bootstrap `("budget-bootstrap", contrast)` (§5, §6.4, §7.3, §12).
