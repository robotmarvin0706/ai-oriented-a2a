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
