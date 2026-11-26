# Protocol-layer value: what the selection and expiry experiments show (non-normative)

> **Status: non-normative.** Summary of experiment 1 of
> [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md). The generated report with every table is
> [`experiments/selection/SELECTION_RESULTS.md`](../experiments/selection/SELECTION_RESULTS.md); if this
> file and the report disagree, the report governs. Nothing here changes `SPEC.md`, `schema/` or the
> WP3 rules in `experiments/timeout_recovery/run.py`.

## Why this file exists

[`docs/WP3_FINDINGS.md`](WP3_FINDINGS.md) explains that WP3 could not separate the envelope from the
host gate: `protocol − plain_file_gated` was 0 by construction, because no envelope field entered the
decision. The selection experiment measures a quantity that is not 0 by construction: a receiver that
uses envelope fields (`prior_outcomes`, `provenance`, its own attributed Absorption Results) to decide
which packets to absorb, against one that receives the same envelopes and ignores them. Content,
parser, gate and host are the same in both arms.

## Result (primary contrast, `naive_host`)

- Verdict (§12): **`supported: envelope-informed selection improves plateau SR by at least the margin`** (R1a).
- `D1 − C` (`history_selection − random_selection`) on `plateau_SR`: **+0.447, 95% CI [+0.401, +0.488]**
  over 160 runs (seeds 42, 7, 2026, 31337 × 40 runs × 200 tasks). Realised half-width 0.043, below the
  0.065 flag.
- V1 content-stub check: **pass** (`C − V1` = +0.143 [+0.107, +0.182]): absorbed content does reach the
  policy.
- V2 envelope-shuffle falsification: **pass** (`V2 − C` = +0.006 [−0.049, +0.059]): with envelope fields
  permuted across packets and content unchanged, informed selection falls back to the random arm, so the
  gain comes from the link between envelope and content.
- Harmful (dishonest-peer) packets absorbed on `naive_host`: 0 in A, B, C, D1, D2, V1 and 1 in V2, out of
  160 runs per arm.

## Reading it correctly

- **The host gate is still doing the safety work.** The dishonest peer's self-reports are good, so
  `history_selection` tries its packets more often than random selection does (263 harmful selections
  in 640 slots for D1, against 125 for C). Every one was rolled back by the isolation gate; D1 then
  recovers through attribution of its own Absorption Results (§7.2). The envelope speeds up finding the
  scarce effective packets; it does not replace the gate.
- **`claim_selection` (D2, secondary) is not misled in SR terms** (+0.463 [+0.419, +0.505]) for the same
  reason: the fabricated claims (0.85–0.95) rank harmful packets near the top, the gate rejects them, and
  the honest effective packets' claims are higher still in this pool. This is a property of this
  simulated pool and gate, not evidence that self-reported claims are trustworthy.
- **It costs tokens.** Over T1–T200, D1 spends about +106k tokens per run more than C (+30k up to its
  plateau), from reading envelopes at every slot (about 24k), more gate runs on candidates, and the
  absorbed block staying in context. `C − B`, the packaging cost of the protocol path, is about +34k per
  run.
- **`working_host` (secondary)**: every contrast is `equivalent within ±0.10 ΔSR`; a host that already
  recovers has almost nothing to gain from any packet, and no harmful packet was absorbed in any arm.
- **Scope**: one simulated tool (`web_search` timeout recovery), a deterministic rule-parser receiver
  (`deterministic-rule-host-v1`), synthetic peers, a 50-packet pool with 3 effective packets and 4
  absorption slots. An LLM receiver, other tools and other pool compositions are not covered.
- **Exchangeability gate (§3, §15.1): the cross-language part is met, third-party adoption is not.**
  `impls/go/` — a clean-room Go implementation (standard library only) written from the normative
  documents, not from this repository's Python code — passes `tests/conformance/` in external mode
  (`63 passed`, 0 failed, 0 skipped, including the 16-thread concurrency case) and reproduces the exact
  `history_selection` and `claim_selection` order of the Python runner on the frozen fixture
  `tests/conformance/fixtures/selection_pools.json` (8 parity tests). Limitation: the Go ranker uses a
  deterministic `packet.id` ascending tie-break where the runner uses a seeded RNG, so ties may be
  ordered differently; the scores and the eligibility rules are identical. Independent third-party
  adoption is still **not** met (0 external authors — the Go implementation lives in this repository and
  is maintained by the same maintainers), so this is not yet an externally adopted protocol.
  `experiments/selection/SELECTION_RESULTS.md` is a generated, frozen artifact of the run and is
  intentionally not edited.

## Experiment 2: expiry

> **Status: non-normative.** Summary of experiment 2, [`docs/PREREG_EXPIRY.md`](PREREG_EXPIRY.md), run
> as merged (no amendment). The generated report with every table is
> [`experiments/expiry/EXPIRY_RESULTS.md`](../experiments/expiry/EXPIRY_RESULTS.md); if this file and
> the report disagree, the report governs. Nothing here changes `SPEC.md`, `schema/`, the WP3 rules in
> `experiments/timeout_recovery/run.py`, or `experiments/selection/`.

Experiment 1 asked which packets a receiver should absorb. Experiment 2 asks when it should stop using a
packet it has already absorbed: part-way through the sequence the simulated tool changes behaviour, and
the recovery policy that was good before the change becomes harmful after it. Only the use of the
machine-checkable `expiry` field differs between the two arms: C (`expiry_blind`) receives the field and
ignores it, D (`expiry_enforced`) enforces it. Content, parser, gate, host, peer feed and task draws are
identical.

- Verdict (§12): **`supported: enforcing machine-checkable expiry improves recovery-window SR by at least the margin`** (R1a).
- `D − C` on `recovery_window_SR` (mean task SR over `T_shift … T_shift + 24`): **+0.167, 95% CI
  [+0.143, +0.192]** over 160 runs (seeds 42, 7, 2026, 31337 × 40 runs × 200 tasks). Realised
  half-width **0.024**, below the 0.034 flag; per-seed means +0.134 to +0.205.
- V1 content-stub check: **pass** (`C − V1` on `pre_ref_SR` = +0.598 [+0.586, +0.606]): absorbed
  content does reach the policy. V2 random-expiry falsification: **pass** (`V2 − C` on
  `recovery_window_SR` = +0.017 [+0.006, +0.029], inside ±0.08): random triggers do not reproduce the
  gain, so it comes from coupling the trigger to the observed shift.
- Next to the headline: `D − C` on `pre_shift_SR` = **−0.020 [−0.024, −0.017]** (secondary; its label is
  `enforcing expiry is worse` for that window) and on `full_sequence_SR` = **+0.015 [+0.010, +0.020]**
  (secondary, R1b, `positive but below the 0.08 margin; not practically significant`). So the post-shift
  gain survives the pre-shift cost over the whole sequence, but only slightly.
- **The false-positive expiries cost success rate before the shift.** D fires 87 expiry events before
  `T_shift` (0.54 per run) and V2 fires 80 (0.50 per run); D's cost is the −0.020 `pre_shift_SR` above,
  and it is reported together with that cost, never on its own.
- **Its total token use is lower.** Over T1–T200 D spends 297,367 tokens per run against C's 360,867,
  and D's stale-policy stretch is shorter: 4.44 stale-policy uses over the recovery window against 17.91,
  with 63.41 extra tool calls against 149.74. `expiry_overhead_tokens`
  (reading and evaluating the field) is 6,127 for D against 1,008 for C and 6,295 for V2; the report
  does not test what causes the total difference. `tasks_to_recover` is 11.3 (median 5.0)
  against 20.6 (median 19.0), `D − C` = −9.244 [−11.213, −7.362]: lower is better here and the
  difference favours D, reported with no verdict (§8.2 assigns no §12 rule to task counts).
- **Secondary contrasts only, no headline.** `D − V2` on `recovery_window_SR` = +0.150 [+0.127, +0.175];
  `C − A` = +0.040 [+0.021, +0.060] (R1b: the blind arm beats the no-sharing baseline by less than the
  margin). Strata: detected shift 118 runs, +0.227 [+0.202, +0.251]; silent shift 42 runs, +0.000
  [+0.000, +0.000] (`equivalent within ±0.08 recovery-window SR`); benign bump 88 runs +0.167, no benign
  bump 72 runs +0.167. Saturation check: C 0.382 and D 0.549 both inside the band [0.10, 0.90] (not
  saturated).
- **The gain is bounded by the share of detected shifts.** The planned silent-shift rate is 25% and the
  planned benign-bump rate 50%; observed here are 26.2% silent, 55.0% benign bump, 73.8% detected. An
  expiry predicate can only act on a shift it can detect, so a silent shift contributes nothing to
  either arm and dilutes the contrast by its share of runs.
- **Caveats.** The `expiry` keys are a non-normative single-project convention (a `tool-version/0` string
  on `web_search` packets), not part of `SPEC.md`; `impls/go/` and no other implementation reads them.
  Scope is one simulated tool (`web_search` timeout recovery) with one simulated, pre-registered regime
  shift (the v2 lease rule), one honest peer and a rule-parser receiver
  (`deterministic-rule-host-v1`) — not an LLM receiver and not a real API. Only R1a is stated as support;
  an R2 verdict would read `equivalent within ±0.08 recovery-window SR` and R4 `undetermined`, and neither
  would support a claim of zero increment.

## Next

Per §15 of the pre-registration: the second implementation is done for the cross-language part (see
above); the expiry experiment is reported above as experiment 2, run as merged with no amendment
(`experiments/expiry/EXPIRY_RESULTS.md`); next is the budget experiment. Wiring `impls/go/` into CI (a Go
toolchain job) would be a separate PR.
