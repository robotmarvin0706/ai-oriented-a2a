# Pre-registration: machine-checkable `expiry` after a tool regime shift (experiment 2)

> **Status: pre-registration. Non-normative.** It changes no wire behaviour, no schema, no
> `SPEC.md` text, no rule in `experiments/timeout_recovery/run.py`, and nothing in
> `experiments/selection/` or [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md).
>
> **No runs before merge.** No run of this experiment, including the pilot in §13, happens
> before the maintainer (`robotmarvin0706`) merges this file into `main`. The rules
> below are frozen at that merge. They are not edited after any data has been seen.
>
> Background: [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md) §15.2,
> [`experiments/selection/SELECTION_RESULTS.md`](../experiments/selection/SELECTION_RESULTS.md),
> [`docs/PROTOCOL_LAYER_VALUE.md`](PROTOCOL_LAYER_VALUE.md).

## 1. Why this experiment

Experiment 1 measured whether envelope fields help a receiver decide **which** packets to
absorb. This experiment measures whether an envelope field helps a receiver decide **when
to stop using** a packet it has already absorbed.

The setting is the one named in [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md) §15.2:
part-way through a task sequence the simulated tool changes behaviour (a simulated API
change), and a recovery policy that was good before the change becomes harmful after it.

The question: **when the only signal is an imperfect, machine-checkable `expiry` field
(some shifts are silent, some version changes are benign), does a receiver that enforces
`expiry` lose less success rate in the tasks right after the shift than a receiver that
receives identical packets and ignores `expiry`?**

Content, parser, gate, host, peer feed and task draws are the same in both arms. Only the
use of `expiry` differs.

## 2. Requirements and where they are applied

This file implements the requirements brief for experiment 2 of 2026-11-26.

| Requirement | Where it is applied |
| --- | --- |
| Docs-only; no schema change; define the `expiry` keys as a non-normative v0.4 convention | §6.1 |
| Avoid "true by construction": no oracle knowledge of `T_shift`; one concrete imperfect signal model with pre-registered parameters | §5.3, §6.2 (version predicate with 25% silent shifts and 50% benign version bumps) |
| State the exact pre-shift and post-shift policies; verify analytically that the primary endpoint does not saturate in either arm | §5.2, §6.3, §9.1, §9.4 |
| Arms A, C, D, V1 (content stub, `C − V1` pre-shift > 0), V2 (random expiry with D's marginal rate, uncoupled from the shift) | §4, §7, §11 |
| Primary endpoint `recovery_window_SR`; `tasks_to_recover` (operational definition, censoring value), `stale_policy_uses`, `extra_tool_calls`, `false_positive_expiries` with pre-shift SR cost, total tokens with expiry overhead as secondary | §8 |
| One margin δ strictly above the planned CI half-width, used for R1a/R1b, R2 and V2 | §10: δ = 0.08 |
| Unit of analysis = run; variance model; power ≥ 0.80 for R1 at effect = δ and R2 at effect = 0 with sensitivity bounds; N, seeds and the noisier-than-planned flag fixed before merge | §9, §10 |
| Rule order `V > R1a > R1b > R3 > R2 > R4`; V2 inconclusive caps the headline at `undetermined` | §12 |
| Wording rules; simulated-tool and rule-parser caveats | §14 |
| List every author-chosen parameter | §16 |
| `experiments/timeout_recovery/` R1–R4 stay frozen | §15 |

## 3. Layers

| Layer | Claim | Form | Status at registration |
| --- | --- | --- | --- |
| Precondition: exchangeable | Two independent implementations interoperate | Boolean (see §15) | Cross-language part met for the transport and the selection envelope (`impls/go/`); the `expiry` convention of §6.1 is **not** read by any second implementation |
| Precondition: verifiable | Absorption has evidence and can be rolled back | Boolean | Met |
| Precondition: attributable | Every policy change traces to a packet | Boolean | Met; expiry reverts are attributed too (§7.1) |
| **Target: capability under change** | Enforcing `expiry` reduces the loss after a tool regime shift | **Effect size on `recovery_window_SR`** | This experiment |

Preconditions are entry gates, not findings, and are reported separately from the verdict.

## 4. Arms

### 4.1 Mapping table

| Label | Arm name in code and results | Packets delivered | `expiry` delivered | Expiry trigger | Strategy model | Isolates |
| --- | --- | --- | --- | --- | --- | --- |
| A | `no_sharing` | none | — | — | — | baseline |
| C | `expiry_blind` | full peer feed (§5.4), over the protocol path | yes, ignored | none | `deterministic-rule-host-v1` | recovery through scheduled slots only |
| D | `expiry_enforced` | same feed, same bytes | yes, enforced | `expiry` predicate fires at the observed tool version (§7.1) | same | **expiry value (D − C), primary** |
| V1 | `expiry_blind_stub` | same feed | yes, ignored | none | stub, `derive() -> {}` | content-stub sanity check (§11.1) |
| V2 | `expiry_random` | same feed, same bytes | yes | random tasks, same number per run as D (§7.3) | same | random-expiry falsification (§11.2) |

- **Primary contrast: D − C** on `recovery_window_SR` (§8.1). It is the only contrast that
  can produce the headline verdict (§12).
- Secondary contrasts, reported with the same rules but not used for the headline: D − C on
  `full_sequence_SR` and on `pre_shift_SR`; D − V2 (coupling of the trigger to the shift);
  C − A; every contrast split by stratum (detected / silent shift; benign bump yes / no).

### 4.2 Host

All arms run on `naive_host` from `experiments/timeout_recovery/host.py`, unchanged:
starting policy `backoff=none,max_attempts=3,drop_stale=False`. `working_host` is not used
(§16): its locally configured policy is the pre-shift peer policy itself, so the shift
would make the host's own configuration stale, which `expiry` on absorbed packets cannot
address.

## 5. Environment

One **run** is one receiver working through one task sequence with one version timeline and
one peer feed. All arms in a run share the run's task draws, version timeline, feed and
gate draws (paired design).

### 5.1 Tasks and slots

- **Tasks.** 200 tasks, T1…T200. Task *t* of a run is `Case(run_seed, t)`, as in
  `experiments/selection/`, with 3 `immediate` and 3 `delayed` evaluation episodes. Task SR
  is the mean success over its 6 episodes. The current policy carries over between tasks.
- **Scheduled slots.** At the start of tasks 1, 26, 51, 76, 101, 126, 151 and 176 (every
  25 tasks, over the whole sequence). Behaviour at a slot is in §7.
- **Gate.** Every absorption attempt, at a slot or in an expired-state fetch (§7.1), goes
  through the unchanged host gate: content policy (SPEC §2.1), isolation (`ISOLATION_N = 3`
  trials, `DSR_THRESHOLD = 0.05` against the **current** policy, no regressed trial),
  single-step rollback, the §3.1 hard gate. Isolation and rollback-check episodes run in the
  regime in force at that task. At most one gate attempt per arm per task.
- Gate constants are those in `host.py` at the merge commit of this file.

### 5.2 Regimes of the simulated tool

- **Regime v1** (tasks before `T_shift`): the `web_search` simulation of `host.py`,
  unchanged.
- **Regime v2** (tasks `T_shift` … T200): v1 plus one **lease rule**. The v2 API issues a
  short-lived session per request; a retry sent after a backoff wait **longer than
  `LEASE_MS = 300` ms** is refused with `session_expired`. Exactly: at an attempt whose
  backoff wait `w` satisfies `w > 300`, the attempt draws its three uniforms as usual, the
  wait is added to the clock, one tool call is counted, then the call fails at once with
  event `session_expired`, `THROTTLE_MS` (100 ms) is added, `pending_stale` is unchanged,
  and the loop continues to the next attempt. Attempts with `w <= 300` (including all
  immediate retries, `w = 0`) are simulated exactly as in v1.

The shift is implemented in `experiments/expiry/` (a new module, §13) and does not modify
`experiments/timeout_recovery/`.

### 5.3 Version timeline (the imperfect signal)

The tool exposes a version string `tool_version` that every arm observes at the start of
every task (a manifest read, like an API version header). Per run:

- At T1 the version is `"v1"`.
- **Benign version bump.** With probability **q_b = 0.50**, one bump at task
  `T_b ~ Uniform{31, …, 70}`: the version becomes `"v1.1"`, the behaviour stays v1.
- **Regime shift** at task `T_shift ~ Uniform{76, …, 125}` (50 values, two slot periods,
  so the shift's position relative to the slot grid is uniform). With probability
  **1 − s = 0.75** the shift is **detected**: the version becomes `"v2"`. With probability
  **s = 0.25** the shift is **silent**: the version string does not change.
- Draws: `T_shift`, then the detection indicator, from the stream
  `("expiry-shift", seed, run)`; the bump indicator, then `T_b`, from
  `("expiry-benign", seed, run)`. Neither stream is used for anything else.

No arm sees `T_shift`. A version predicate fires at a benign bump (false positive, a real
pre-shift cost, §9.3) and does not fire at a silent shift (false negative).

### 5.4 Peer feed

One honest peer, `peer-tool-1`, uses the same tool and publishes into a feed that every arm
(except A) reads. Each packet is a v0.4 Experience Packet with `type = "failure_mode"`,
`absorption = {"method": "prompt_injection", "target": "tool_retry_policy"}`,
`provenance.source_ref = "peer:peer-tool-1"`, the `recovery_policy` content format of
`experiments/timeout_recovery/packets.py`, and the `expiry` object of §6.1 naming the
version the peer observed when it published. No `prior_outcomes` or `verification_claim`
is sent (selection is not studied here).

| Packet | Published at the start of | `recovery_policy` content | `expiry.tool_version_neq` |
| --- | --- | --- | --- |
| P1 | before T1 | exponential backoff, `base_backoff_ms` = 400, `max_attempts` = 4, `drop_stale_partials` = true (`PEER_POLICY` of `packets.py`) | `"v1"` |
| P1′ (only with a benign bump) | `T_b + 5` | identical to P1 | `"v1.1"` |
| P2 | `T_shift + 5` | exponential backoff, `base_backoff_ms` = 150, `max_attempts` = 3, `drop_stale_partials` = true | the version observed at `T_shift + 5` (`"v2"` if detected; `"v1"` or `"v1.1"` if silent) |

- **Peer lag L = 5 tasks** after every version change or regime shift. The peer always
  publishes P2 at `T_shift + 5`, also after a silent shift (it notices the failures, not
  the version string). So the content needed to recover is available to every arm at the
  same task; detection affects only the trigger.
- Packet ids: `pkt-exp-<run_seed>-p1`, `-p1b`, `-p2`. Feed order is publication order;
  "newest" means latest published.
- Every feed packet must pass `content_policy` (SPEC §2.1). A `content_policy:` rejection
  of a feed packet is a generator bug: the run is discarded and fixed before the main run,
  not counted.

## 6. The `expiry` convention and the signal model

### 6.1 Convention (non-normative, v0.4)

[`schema/experience-packet.schema.json`](../schema/experience-packet.schema.json) already
defines `"expiry": { "type": "object" }` (line 31), an open object. This experiment uses the
following keys inside it. This is a **non-normative v0.4 convention** for this experiment
only; it adds nothing to `SPEC.md` or `schema/` and no receiver is required to read it.

```json
"expiry": {
  "convention": "tool-version/0",
  "tool": "web_search",
  "tool_version_neq": "v1"
}
```

- **Semantics.** The packet is **expired** for a receiver when the receiver's currently
  observed version string of `tool` is not equal to `tool_version_neq`. Otherwise it is
  valid.
- **Not evaluable.** If `convention` is missing or different, `tool` names a tool the
  receiver does not use, or `tool_version_neq` is missing or not a string, the predicate is
  not evaluable: it never fires and the event is logged as `expiry_unevaluable`. The feed
  of §5.4 never produces such a packet; a non-zero count is a bug, handled like a
  `content_policy` rejection.
- C and V1 receive the same bytes and ignore the object.

### 6.2 Why this signal is imperfect, with fixed parameters

The predicate is coupled to the shift only through the version string. Its errors are
pre-registered:

| Parameter | Value | Effect |
| --- | --- | --- |
| Silent-shift rate `s` | 0.25 | In 25% of runs D cannot react to the shift; D and C are expected to behave identically in the recovery window |
| Benign-bump probability `q_b` | 0.50 per run (at most one bump) | D reverts a still-good policy and runs naive until P1′ is published 5 tasks later: a real pre-shift SR cost |
| Peer lag `L` | 5 tasks | After a detected shift D runs the naive policy (not P2) for 5 tasks; enforcing expiry removes the stale policy but cannot deliver the new one sooner |

D therefore cannot reach the oracle schedule "switch to P2 exactly at `T_shift`". Its
expected advantage is computed, not assumed, in §9.

### 6.3 Policies and their success rates, per regime

Let `b ~ Uniform(0.55, 0.85)` be a case's base timeout probability (`BASE_P_RANGE`); the
`delayed` episodes use `b + 0.10` (`DELAYED_DRIFT`); task SR is the mean of the two. With
the loop of `host.py` (stale partial results arrive first with probability 0.5; a wait `w`
scales the timeout probability by `exp(−w/1000)`; immediate retries add 0.05 each, capped
at 0.98; timeouts cost 1500 ms; deadline 8000 ms; latency uniform in 200–900 ms):

| Policy | Per-episode success, as a function of `b` | Task SR, v1 | Task SR, v2 |
| --- | --- | --- | --- |
| naive (host start) | `(1−b) + 0.5·b·(1−b′) + 0.25·b·b′·(1−b″)`, `b′ = min(0.98, b+0.05)`, `b″ = min(0.98, b+0.10)` | 0.340 | 0.340 (no backoff wait, lease never applies) |
| P1 (pre-shift peer policy) | v1: `1 − b·(b e^−0.4)·(b e^−0.8)·[b e^−1.6 + (1 − b e^−1.6)·2/7]` (the 4th attempt starts at 7300 ms, so it meets the deadline only if latency ≤ 700 ms) | **0.947** | v2: every retry waits ≥ 400 ms > 300 ms, so only the first call can succeed: `1 − b` → **0.250** |
| P2 (post-shift peer policy) | `1 − b³ e^−0.45` (waits 150 and 300 ms, never refused) | 0.717 | **0.717** |

After the shift, the stale P1 (0.250) is **worse than having absorbed nothing** (0.340), and
the post-shift policy P2 (0.717) is the best policy available in the feed.

**Validation against committed data (not a run).** The same formulas give 0.340 for naive
in v1, against `no_sharing` AUC 0.340 and plateau 0.337 on `naive_host` in
[`experiments/selection/SELECTION_RESULTS.md`](../experiments/selection/SELECTION_RESULTS.md);
and 0.947 for P1 in v1, against `no_sharing` AUC 0.947 and plateau 0.946 on `working_host`
(whose policy is `PEER_POLICY` = P1). Gate acceptance is computed below and matches the
25–27 of 30 that `distilled_packet` (also P1) achieves in WP3.

**Gate acceptance (planning).** In every pair below the new policy's success set contains
the old policy's on shared draws (same first call; later attempts wait longer or drop stale
results), so the gate's regression check never fires and acceptance is
`a = E_b[1 − (1 − q(b))³]` with `q(b)` the per-episode success difference in the isolation
window (no drift):

| Attempt | Regime | `a` |
| --- | --- | --- |
| P1 over naive (T1 slot; D re-absorbing P1′; V2 re-absorbing) | v1 | 0.897 |
| P2 over stale P1 (C, and D after a silent shift) | v2 | 0.852 |
| P2 over naive (D after expiry) | v2 | 0.741 |

D's lower acceptance is part of the planning model, not ignored.

## 7. Arm behaviour

At the start of each task *t*, every arm executes these steps in order; A does only step 1
and step 6.

1. Observe `tool_version` (§5.3). Packets published at or before *t* are in the feed.
2. **Expiry trigger.** D: if the installed policy came from packet *p* and *p*'s `expiry`
   is evaluable and expired at the observed version, an **expiry event** occurs. V2: an
   expiry event occurs if *t* is one of its trigger tasks (§7.3). C, V1: never.
3. **Expiry event.** If the installed policy came from a packet, single-step revert to the
   host start policy (naive), attribute the revert to the packet id with a local result
   `expired`, and enter the **expired state**. If the installed policy is already the start
   policy, the event is counted and nothing changes.
4. **Expired-state fetch** (D, V2, while in the expired state): take the newest feed packet
   whose `expiry` is valid at the observed version and that has not been tried during this
   expired state, and send it through the gate. If absorbed, leave the expired state.
5. **Scheduled slot** (only at a slot task, and only if step 4 ran no gate this task): take
   the newest feed packet that (D, V2) is valid at the observed version / (C, V1) any
   packet, and whose `content` differs from the content behind the installed policy (the
   start policy if none). If one exists, send it through the gate. A packet rejected
   earlier may be tried again at a later slot.
6. Run the task's 6 episodes with the installed policy in the current regime.

### 7.1 What each arm does in each situation (planning)

| Situation | C | D |
| --- | --- | --- |
| T1 | absorbs P1 (a = 0.897; if rejected, retries at T26) | same |
| Benign bump at `T_b` | keeps P1 (still good); at later slots P1′ has the same content, so nothing is tried | P1 expires: naive for `T_b … T_b+4`; fetches P1′ at `T_b + 5` (a = 0.897) |
| Detected shift | keeps stale P1 until the first slot at or after `T_shift + 5`, then tries P2 (a = 0.852) | P1 expires at `T_shift`: naive for 5 tasks; fetches P2 at `T_shift + 5` (a = 0.741); if rejected, retries at C's slot |
| Silent shift | as detected | identical to C (nothing fires) |

### 7.2 Token accounting for expiry

- Every arm except A is charged, at every scheduled slot, the `approx_tokens` of the
  envelopes (every field except `content`) of all feed packets (the feed read).
- D and V2 are additionally charged, at every task with a packet-derived policy installed,
  `approx_tokens({"expiry": <installed packet's expiry>, "tool_version": <observed>})` (the
  expiry check), and at every expired-state fetch the feed read above.
- These charges are reported as `expiry_overhead_tokens` and included in total tokens.

### 7.3 V2 trigger

Per run, let `k` be the number of expiry events of D in that run. V2's trigger tasks are
`k` distinct tasks drawn uniformly from T2–T200 with the stream `("expiry-v2", seed, run)`,
independent of `T_shift`, `T_b` and detection. Everything else in V2, including the
validity filter in steps 4 and 5, is D's. So V2 has D's marginal expiry rate per run, and
only the coupling of the trigger to version changes is removed.

## 8. Endpoints

### 8.1 Primary endpoint

**`recovery_window_SR`**: the mean task SR over the recovery window
`T_shift … T_shift + 24` (K = 25 tasks; equivalently the normalised post-shift AUC over K
tasks). It is continuous, never censored, defined for every run and arm, and `T_shift + 24
<= 149`, so it always lies inside the sequence.

### 8.2 Secondary endpoints (reported, no verdict)

- **`tasks_to_recover`**: `t_rec − T_shift`, where `t_rec` is the first task `>= T_shift`
  run with a policy absorbed from a packet published at or after `T_shift` (P2).
  **Censored at 75**: if no such task occurs by `T_shift + 74`, the value is 75 and the run
  is flagged censored. Reported per arm as mean (censored values at 75), median and number
  censored, and D − C with the paired bootstrap of §12. The literal §15.2 wording
  ("tasks needed to recover the pre-shift SR") is not used: the best post-shift policy
  reaches 0.717 against 0.947 before the shift (§6.3), so that definition would censor
  every run.
- **`stale_policy_uses`**: number of tasks `>= T_shift` run with a policy absorbed from a
  packet published before `T_shift`; reported over the recovery window and over
  `T_shift … T200`.
- **`extra_tool_calls`** during recovery: tool calls over the recovery window (task
  episodes, isolation and rollback-check episodes) minus arm A's task-episode tool calls on
  the same tasks.
- **`false_positive_expiries`**: expiry events before `T_shift` (D: benign bumps; V2:
  random triggers), per run and in total; and the **pre-shift SR cost**, D − C and V2 − C on
  `pre_shift_SR` (mean task SR over T1 … `T_shift − 1`).
- **`full_sequence_SR`**: mean task SR over T1–T200, so the pre-shift cost and the
  post-shift gain are visible in one number.
- **`pre_ref_SR`**: mean task SR over T26–T75 (always before any shift), used by V1.
- **Total tokens** over T1–T200, and `expiry_overhead_tokens` (§7.2) separately.
- Attribution rate (policy changes and expiry reverts traceable to a packet id with a
  matching result). Process metric, not capability.
- Strata: detected / silent shift; benign bump yes / no. Descriptive, no verdict.

## 9. Sample size and power

The unit of analysis is the **run**. Tasks in a run share one version timeline and one
sequence of absorption decisions, so they are not independent replicates.

### 9.1 Variance model of D − C on `recovery_window_SR`

Write `N = 0.340`, `S = 0.250`, `P = 0.717` (§6.3). Let `c = 5 + u` be the offset from
`T_shift` of C's first slot at or after `T_shift + 5`; because `T_shift` is uniform over two
slot periods, `u ~ Uniform{0, …, 24}`.

- **Silent shift** (probability 0.25): D and C run the same policies on the same draws in
  the window, so `Δ = 0` (except in the rare runs where D failed to re-absorb P1′ after a
  benign bump and starts the window on the naive policy).
- **Detected shift** (probability 0.75), with all gate attempts accepted: C runs stale P1
  for `m = min(c, 25)` window tasks and P2 for the rest; D runs naive for 5 tasks and P2
  for 20. So

  ```
  Δ_det(u) = [5·N + 20·P − m·S − (25 − m)·P] / 25
  E[m] = (Σ_{u=0}^{19} (5 + u) + 5·25) / 25 = (290 + 125) / 25 = 16.6
  E[Δ_det] = [1.700 + 14.340 − 4.150 − 6.023] / 25 = 0.235
  ```

- **Gate rejections** (§6.3): C's P2 attempt is accepted with 0.852, otherwise C is stale for
  the whole window; D's fetch is accepted with 0.741, otherwise D stays naive until C's
  slot and retries there with 0.741. Enumerating `u` and the three gate outcomes exactly
  gives `E[Δ_det] = 0.204`.
- **Task noise.** On window tasks where the two arms run different policies, the per-task
  paired SD (6 episodes plus between-case variation of `b`) is 0.120 (naive vs stale), 0.204
  (P2 vs stale) and 0.199 (P2 vs naive); it adds `Σ sd² / 25²` per run, 0.00065 on average.

Result: **E[D − C] = 0.75 × 0.204 = 0.153; run-level SD σ = 0.161.** A Monte Carlo of the
same planning model (40,000 simulated runs of the formulas above, not of the simulator)
with gate draws treated as independent across arms, which is conservative, gives
E = 0.152 and SD = 0.168. **Planning value σ = 0.18**, sensitivity **0.14 to 0.22**.

### 9.2 Power

Two-sided 95% CI (α = 0.05), normal approximation, `SE = σ / √R`, δ = 0.08 (§10).
Power for R1 (CI lower bound > 0) when the true D − C is δ: `Φ(δ/SE − 1.96)`. Power for
R2 (CI inside ±δ) when the true D − C is 0: `2Φ(δ/SE − 1.96) − 1`. Both use the planning σ
for the alternative and the null (conservative for the null, whose SD is smaller).

| R (runs) | σ = 0.14: R1 / R2 | σ = 0.18 (plan): R1 / R2 | σ = 0.22: R1 / R2 |
| --- | --- | --- | --- |
| 80 (4 × 20) | 0.999 / 0.998 | 0.978 / 0.956 | 0.902 / 0.804 |
| 120 (4 × 30) | 1.000 / 1.000 | 0.998 / 0.996 | 0.979 / 0.957 |
| **160 (4 × 40, registered)** | **1.000 / 1.000** | **1.000 / 1.000** | **0.996 / 0.992** |

80% power needs `R >= (2.802 σ / δ)²` for R1 and `R >= (3.242 σ / δ)²` for R2: 40 and 54
runs at σ = 0.18, 60 and 80 runs at σ = 0.22. The registered 160 runs keep both above 0.99
over the whole sensitivity range, and leave room for the secondary and validity contrasts.

**Registered sample: seeds 42, 7, 2026, 31337 × 40 runs (run index 0–39) × 200 tasks = 160
runs, 32,000 tasks per arm.** Run seeds are `stable_int("expiry", seed, run)`. Planning
values are not re-estimated from the data, and N is not changed after the pilot.

### 9.3 Other planned values (same planning model, for the reader)

| Quantity | Planned value |
| --- | --- |
| Mean `recovery_window_SR`: A / C / D | 0.340 / 0.384 / 0.535 |
| D − C on `pre_shift_SR` (benign false-positive cost) | −0.019 |
| D − C on `full_sequence_SR` | +0.013 |
| C − V1 on `pre_ref_SR` (§11.1) | +0.603 |
| V2 − C on `recovery_window_SR` (§11.2) | +0.016 (conservative SD 0.116) |
| D − V2 on `recovery_window_SR` | +0.135 |

A benign bump costs D about 5 × (0.947 − 0.340) ≈ 3 task-successes before the shift. Over
the whole sequence the planned net gain of enforcing expiry (+0.013) is far smaller than the
gain in the recovery window; both are reported (§14).

### 9.4 Non-saturation

In regime v2 the feed holds no policy better than P2 (0.717) and none worse than stale P1
(0.250), so every arm's expected `recovery_window_SR` lies in [0.250, 0.717] by
construction of §6.3. Planned per-run values: C from 0.25 (stale for the whole window) to
0.62, D from 0.25 to 0.64; means 0.384 and 0.535. Neither arm can approach 0 or 1. If a
realised arm mean of C or D lies outside [0.10, 0.90], the report says that the endpoint
saturated contrary to plan; the decision rules are not changed.

## 10. Margin and noise floor

**δ = 0.08 recovery-window SR, one value used everywhere in this experiment**: the R1 grade
and R2 equivalence bound (§12), the V2 band (§11.2) and the power target (§9). No other
margin appears in this file. It is chosen for this experiment, not carried over from WP3
(`WP3_R2_EQ_MARGIN = 0.05`, unchanged) or from experiment 1 (δ = 0.10, unchanged).

- **Practical meaning.** 0.08 = 2/25: the equivalent of two fully successful tasks out of the
  25 in the recovery window.
- **Noise floor.** The planned 95% half-width of the primary contrast at R = 160 is
  **0.025** at σ = 0.161, **0.028** at the planning σ = 0.18 and **0.034** at σ = 0.22. δ is
  strictly above all of them, by a factor of at least 2.3.
- **Noisier-than-planned flag.** If the realised half-width of the primary contrast exceeds
  **0.034**, the report says that the run was noisier than planned.
- A realised CI wider than ±δ cannot satisfy R2, so an underpowered run cannot be read as
  equivalence.

## 11. Validity checks

Both checks use the paired bootstrap of §12.

### 11.1 V1: content-stub sanity check

V1 is C with a strategy model whose `derive()` returns `{}`: packets are delivered, gated
and attributed as in C, but nothing usable reaches the policy, so V1 stays naive.

- **Pass**: the CI of `C − V1` on `pre_ref_SR` (T26–T75, before any shift) has lower bound
  > 0. Planned: +0.603.
- **Fail**: otherwise. Absorbed content does not reach SR, so the experiment cannot measure
  anything about stale content. The result is **VOID**.

### 11.2 V2: random-expiry falsification

V2 fires expiry at D's per-run rate on tasks unrelated to any version change (§7.3). If the
benefit of D came from reverting and re-fetching at any time rather than from the predicate
tracking the shift, V2 would recover as well as D.

A random revert inside the stale period also helps a little, because stale P1 (0.250) is
worse than naive (0.340); the planned V2 − C is +0.016. The check is therefore graded with
δ, not with "contains 0":

- **Pass**: the 95% CI of `V2 − C` on `recovery_window_SR` lies inside [−δ, +δ] =
  [−0.08, +0.08].
- **VOID**: V2 would itself meet R1a against C: CI lower bound > 0 **and** point estimate
  `>= δ`. Random expiry then explains a gain of the size the experiment would call support.
- **Inconclusive**: anything else (CI reaching beyond ±δ without meeting VOID). The
  headline verdict is capped at `undetermined`.

D − V2 is reported as a secondary contrast.

## 12. Analysis and decision rules

**Estimate.** For each run, `Δ = recovery_window_SR(D) − recovery_window_SR(C)`. The
estimate is the mean over the 160 runs. **CI**: paired percentile bootstrap over runs,
stratified by seed (runs resampled within each of the four seeds), B = 2000, 95%, seeded
with `stable_int("expiry-bootstrap", contrast)`. Per-seed means are reported alongside,
without a per-seed verdict.

Rules are applied in this order. The first that matches is the verdict.

| Rule | Condition | Verdict label |
| --- | --- | --- |
| V | V1 fails, or V2 is VOID | `VOID` (the reason is stated) |
| R1a | CI lower bound > 0 and point estimate ≥ δ | `supported: enforcing machine-checkable expiry improves recovery-window SR by at least the margin` |
| R1b | CI lower bound > 0 and point estimate < δ | `positive but below the 0.08 margin; not practically significant` |
| R3 | CI upper bound < 0 | `enforcing expiry is worse` |
| R2 | CI inside [−δ, +δ] | `equivalent within ±0.08 recovery-window SR` |
| R4 | otherwise | `undetermined` |

**Headline.** Only R1a may be stated as support for `expiry`. An R1b result is reported
with its label verbatim and must not be called "supported", in the headline or elsewhere.

If V2 is inconclusive (§11.2), R1a, R1b, R3 and R2 are reported as information and the
headline verdict is `undetermined`.

The same table is computed for each secondary contrast and labelled secondary. No headline
statement may rest on a secondary contrast or on one stratum.

## 13. Procedure

1. Merge this file (maintainer, `robotmarvin0706`). Rules frozen.
2. Implement `experiments/expiry/` as a new module that reuses `Host`, `Case`, the strategy
   models, `approx_tokens`, `stable_int` and the gate without modifying
   `experiments/timeout_recovery/` or `experiments/selection/`; the v2 lease rule lives in
   the new module. The implementation PR states the commit of this file it implements and
   includes unit tests for the lease rule, the `expiry` predicate (including
   `expiry_unevaluable`) and the arm steps of §7. If a rule here cannot be implemented as
   written, an amendment PR to this file is merged **before any run**; the amendment is
   listed at the top of the results.
3. Pilot: seed 42, run indices 100–101, 200 tasks, to check that the pipeline runs and that
   V1/V2 compute. Pilot runs are not part of the analysis, and their numbers are not used
   to change any rule, N, δ or planning value. A bug found in the pilot is fixed in code and
   recorded in the results.
4. Main run: the 160 runs of §9 on `naive_host`, all arms of §4.1. Output to `/tmp` unless
   committing data with explicit flags, following the existing runner convention.
5. Write `experiments/expiry/EXPIRY_RESULTS.md` from the runner (generated, not
   hand-edited), then update [`docs/PROTOCOL_LAYER_VALUE.md`](PROTOCOL_LAYER_VALUE.md).

## 14. Reporting and wording

| Term | Meaning | Not allowed |
| --- | --- | --- |
| `recovery_window_SR` | Mean task SR over `T_shift … T_shift + 24` | "capability" on its own; "recovery" without the window |
| `D − C` | The increment from enforcing the `expiry` predicate | "the protocol has no effect" when the verdict is R2 |
| `equivalent within ±0.08 recovery-window SR` | R2 | "no measurable increment", "no effect", "expiry is useless" |
| `undetermined` | CI crosses the margin, or V2 inconclusive | "no increment", "no effect" |
| false-positive expiry | An expiry event before `T_shift` | "harmless" without the pre-shift SR cost |
| attribution rate | Share of policy changes and reverts traceable to packet id + result | "accountable" as a capability claim |

Every result states: the verdict label; the point estimate and CI of D − C; V1 and V2
outcomes; the half-width flag; D − C on `pre_shift_SR` and on `full_sequence_SR` next to the
headline; the silent-shift and benign-bump rates (25%, 50%) and that the gain is bounded by
the share of detected shifts; that the `expiry` keys are a non-normative single-project
convention not read by any second implementation; and that the scenario is one simulated
tool (`web_search` timeout recovery) with one simulated, pre-registered regime shift (the
v2 lease rule), one honest peer, and a rule-parser receiver (`deterministic-rule-host-v1`),
not an LLM receiver or a real API.

## 15. Relation to existing artifacts and order of work

- `experiments/timeout_recovery/run.py` R1–R4 (WP3) and their tests are **frozen**. This
  experiment adds no rule to them and changes none of their constants.
- `experiments/selection/` and [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md) are not
  changed. Their δ = 0.10 does not apply here.
- Exchangeability: `impls/go/` passes `tests/conformance/` in external mode and matches the
  selection ranking (see [`docs/COMPLIANCE.md`](COMPLIANCE.md)); it is maintained by the same
  maintainers and does not read the `expiry` convention of §6.1. Until a second
  implementation evaluates that convention, results must call it a single-project
  convention, not an interoperable protocol feature. Extending `impls/go/` is not part of
  this registration.
- Next: experiment 3, budget (`docs/PREREG_BUDGET.md`), with its own margin and its own
  merge before any run, as in [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md) §15.2.

## 16. Decisions for review

Decisions made here that the requirements brief did not fix, listed so a reviewer can check
them before merge:

1. **Signal model**: version predicate (`tool_version_neq`) rather than a noisy TTL (§6).
2. **Convention keys**: `convention = "tool-version/0"`, `tool`, `tool_version_neq`;
   non-evaluable objects never fire and are logged (§6.1).
3. **Regime shift**: the v2 lease rule, `LEASE_MS = 300`, a refused retry costs one call and
   `THROTTLE_MS`, consumes an attempt and leaves `pending_stale` unchanged (§5.2).
4. **Policies**: P1 = the existing `PEER_POLICY` (400 ms, 4 attempts, drop); P2 = 150 ms,
   3 attempts, drop (§5.4, §6.3).
5. **Silent-shift rate** s = 0.25; **benign-bump probability** q_b = 0.50, at most one bump
   per run, `T_b ~ Uniform{31, …, 70}` (§5.3).
6. **Shift timing** `T_shift ~ Uniform{76, …, 125}` (two slot periods) (§5.3).
7. **Peer lag** L = 5 tasks after every version change; the peer publishes P2 at
   `T_shift + 5` also after a silent shift; one honest peer, no dishonest peer (§5.4).
8. **Expiry handler**: revert to the host start policy (not keep the stale policy until a
   replacement arrives); fetch every task while expired; each packet tried once per expired
   state; scheduled slots may retry (§7).
9. **Slots** every 25 tasks over the whole sequence (T1 … T176), and a content-equality no-op
   at slots for every arm (§5.1, §7).
10. **At most one gate attempt per arm per task**; the expired-state fetch takes precedence
    over a slot (§7).
11. **V2 trigger**: same number of events as D per run, uniform over T2–T200, D's validity
    filter kept (only the trigger is ablated) (§7.3).
12. **V2 rule differs from experiment 1**: no "CI contains 0" condition; VOID when V2 itself
    meets R1a against C. Reason: a random revert inside the stale period has a small,
    analytically positive effect (+0.016 planned), so "contains 0" could void a correct
    design (§11.2).
13. **V1** is measured on `pre_ref_SR` (T26–T75) (§11.1).
14. **Primary endpoint** `recovery_window_SR` with K = 25 (§8.1).
15. **`tasks_to_recover`** is process-based (first task on a policy from a packet published
    at or after `T_shift`), censored at 75; the literal "recover the pre-shift SR" would
    censor every run (§8.2).
16. **δ = 0.08** (2 of 25 window tasks) (§10).
17. **Noisier-than-planned flag** at a realised half-width > 0.034 (§10).
18. **Planning σ = 0.18**, sensitivity 0.14–0.22; **N = 160 runs** (seeds 42, 7, 2026, 31337
    × run indices 0–39), 200 tasks; run seed `stable_int("expiry", seed, run)` (§9).
19. **Host**: `naive_host` only; `working_host` dropped (§4.2).
20. **Token charges** for the feed read at slots (all arms but A), the per-task expiry check
    and the expired-state fetch (D, V2) (§7.2).
21. **Planning values** come from the closed-form success rates of §6.3, validated against
    committed results, and not from any run of this experiment (§6.3, §9).
22. **Saturation report**: a realised C or D mean outside [0.10, 0.90] is reported as
    saturation contrary to plan, without changing the rules (§9.4).
23. **Random streams**: `("expiry-shift", seed, run)`, `("expiry-benign", seed, run)`,
    `("expiry-v2", seed, run)`, bootstrap `("expiry-bootstrap", contrast)` (§5.3, §7.3, §12).
