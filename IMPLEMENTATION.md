# Implementation guidance (non-normative)

This document does not change wire requirements in `SPEC.md`. It records preferred host-side practice for a minimal two-endpoint implementation.

## Priority path

Prefer a measurable absorption loop and fixed-budget comparison over expanding Schema or discovery first.

## Absorption loop

- After absorption, the receiver MUST run a local regression of the same failure scenario before treating the change as kept.
- Write baseline and outcome into `effect_summary` even though SPEC marks that field OPTIONAL. Implementations SHOULD treat recording as mandatory for this experiment path.
- Support only MUST methods first (`prompt_injection`, `retrieval`). Support `trajectory_replay` only when a clear replay path exists and cost is bounded; otherwise reject with an explicit reason.

## Sender distillation

- Keep decision points and outcomes only: failure mode plus successful recovery strategy.
- Do not ship full raw trajectories by default; keep packet size small.

## Isolation and rollback

- After absorption, run N isolated trials before promoting the change. N is declared by the implementation; suggested minimum is 3.
- If performance drops past a declared threshold, convert to `rejected` and roll back automatically.
- Optional AbsorptionDelta identity and single-step rollback: see [docs/ABSORPTION_STATE.md](docs/ABSORPTION_STATE.md) (non-normative).

## Comparison experiment (tool-call timeout / failure recovery)

Four arms under a fixed budget:

- No sharing
- Own history only
- Share raw trajectories
- Share distilled Experience Packets

Record at least:

- Recovery success-rate delta (ΔSR)
- Extra tokens / tool calls
- Immediate vs delayed post-absorption performance gap
- Regression magnitude and time-to-recover

See also `SPEC.md` §5.

## Not priority

Until there is positive net ΔSR evidence, do not expand use of `conditional_packet` / `constraint_scope`, and do not prioritize interop with official A2A (a2aproject).


## Local Net ΔSR Experiment Guide (Non-normative)

This section does not alter v0.2 wire-protocol requirements and does not mandate that any implementation execute the experiment. It provides a reproducible single-machine procedure for a receiver to test whether MUST absorption (`prompt_injection` or `retrieval`) yields a measurable net-positive ΔSR under a fixed token budget.

### Goal
On the tool-call timeout / failure-recovery scenario, use a four-arm comparison to determine whether a distilled Experience Packet produces a net gain relative to `no_sharing` and `own_history`, with near-zero rollback cost. All thresholds and logs remain under local receiver control.

### Case Set (single-machine, quickly generated)
- Scale: start with 40 reproducible cases (expandable to 80).
- Generation: a local script constructs controlled timeouts (forced delay or direct timeout error), covering patterns such as retry loops without backoff and stale results that are not discarded.
- Each case fixes: trigger input, timeout condition, success criteria, and random seed.
- Generation time: typically a few seconds. The same seed yields identical results. The case set is a local experimental asset only.

### Budget and Thresholds (suggested starting configuration, adjustable)
- Per-case, per-arm total token budget for absorption + regression: 3000.
- Absorption-only budget: 800 tokens. Exceeding the limit terminates that case/arm.
- Relative ΔSR threshold: ≥ 0.05 (relative to the case baseline).
- Extra-token cost cap: ≤ 1.5× the `own_history` arm.
- Isolation trials N: 3. All must pass before promotion.
- Dynamic adjustment: if rollbacks are frequent, the receiver may temporarily raise the ΔSR threshold by 0.02 and record the reason and round. All changes stay in local logs.

### Four Arms
- `no_sharing`: pure local baseline.
- `own_history`: receiver’s own history only.
- `raw_trajectory`: absorb the peer’s full trajectory.
- `distilled_packet`: absorb the compressed Experience Packet (MUST method).

### Per-case Flow
1. Record baseline (run without absorption; obtain SR and tokens).
2. Perform absorption in an isolated context (if required) and record absorption cost.
3. Immediately regress and run N isolation trials.
4. Compute average ΔSR and extra cost.
5. If thresholds are not met, roll back (delete the injected fragment or retrieval entry in a single step) and mark rejected.
6. Promote only on full pass; optionally measure delayed decay.

### Value Judgment (after the stage)
The path is considered to have current net value only when all of the following hold:
- `distilled_packet` average ΔSR ≥ 0.05 and not lower than `own_history`;
- extra tokens ≤ 1.5× `own_history`;
- post-rollback performance recovers to near baseline in one step;
- results are reproducible across two independent runs.

Otherwise, stop investing in this direction and do not proceed to identity, subscription, or other protocol extensions.

### Execution Order
- Stage 1: run all four arms on the timeout scenario only and judge.
- Stage 2 (optional): reuse the same framework with a second failure type (e.g., tool-parameter hallucination) to check transferability. Do not enter Stage 2 if Stage 1 fails.

### Relation to Existing Code
May be extended from `examples/minimal_dual_end.py`. A non-normative demonstration script `examples/net_delta_sr_experiment.py` is suggested. Full logs remain local by default.

### Testing the Experiment
1. Generate the case set with a fixed seed and confirm identical output on re-run.
2. Execute Stage 1 (timeout only) under the token budgets above.
3. Inspect per-arm average ΔSR, extra tokens, acceptance rate, and rollback success.
4. Re-run the entire Stage 1 with a different seed; require directional consistency on the distilled_packet vs own_history comparison.
5. (Optional) Swap the case generator for a second failure type and repeat; record whether the net-gain conclusion transfers.
6. Confirm that all logs and threshold adjustments remain local and that no new protocol fields are emitted.
