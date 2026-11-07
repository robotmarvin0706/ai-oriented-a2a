# Absorption delta identity and single-step rollback (non-normative)

This document does not change wire requirements in `SPEC.md`. It describes host-local state for an optional AbsorptionDelta produced by one absorption, so the change can be uniquely identified and rolled back in one step.

## AbsorptionDelta

One absorption MAY produce one AbsorptionDelta. A delta MUST be uniquely identifiable and MUST support single-step rollback to the pre-absorption state.

Fields:

- `delta_id`: receiver-local unique id
- `packet_id`: source Experience Packet id
- `applied_at`: timestamp
- `scope`: effective scope of the change
- `rollback_token`: opaque token used for precise undo

## Activation rules

- Mark a delta `active` only after the receiver's light-weight verification passes and no rollback condition has fired.
- The same `packet_id` MUST NOT be activated twice while a prior activation for that packet remains `active`.

## Single-step rollback

- Provide `rollback(delta_id)` that undoes only the changes introduced by that delta and leaves other active deltas untouched.
- After rollback, set status to `rolled_back` and record a reason.

## Persistence (minimum)

- Maintain at least a local `active_deltas` list.
- Reload it after process restart.
- Distributed consistency is NOT required.

## Relation to ΔSR

- Any externally reported net gain MUST be traceable to a concrete `delta_id`.
- If metrics fall past a declared threshold after rollback, that delta MUST NOT auto-activate again.
