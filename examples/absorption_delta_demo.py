#!/usr/bin/env python3
"""
Non-normative demonstration of AbsorptionDelta identity and single-step rollback.

Local dicts only. Does not change wire requirements in SPEC.md.
Run from repo root:
  python3 examples/absorption_delta_demo.py
"""

import json
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_delta(state, packet_id, scope, rollback_token):
    """Create one delta and store it in active_deltas.

    Refuses a second activation of the same packet_id while a prior
    activation for that packet remains active.
    """
    for delta in state["active_deltas"]:
        if delta["packet_id"] == packet_id:
            raise ValueError(f"packet_id already active: {packet_id}")

    delta_id = f"delta-{len(state['history']) + 1}"
    delta = {
        "delta_id": delta_id,
        "packet_id": packet_id,
        "applied_at": _now(),
        "scope": scope,
        "rollback_token": rollback_token,
        "status": "active",
    }
    state["history"].append(delta)
    state["active_deltas"].append(delta)
    state["applied_scopes"][delta_id] = scope
    return delta


def rollback(state, delta_id, reason):
    """Undo only the changes introduced by delta_id.

    Other active deltas stay untouched. Status becomes rolled_back
    and the reason is recorded on that delta.
    """
    target = next((d for d in state["history"] if d["delta_id"] == delta_id), None)
    if target is None or target["status"] != "active":
        raise KeyError(f"no active delta: {delta_id}")

    state["applied_scopes"].pop(delta_id, None)
    target["status"] = "rolled_back"
    target["reason"] = reason
    state["active_deltas"] = [
        d for d in state["active_deltas"] if d["delta_id"] != delta_id
    ]
    return target


def main():
    state = {"active_deltas": [], "history": [], "applied_scopes": {}}

    apply_delta(state, "pkt-timeout-recovery", "tool_call_timeout", "opaque-undo-1")
    apply_delta(state, "pkt-stale-result", "stale_result_discard", "opaque-undo-2")
    print("=== After apply ===")
    print(json.dumps(state, indent=2, ensure_ascii=False))

    try:
        apply_delta(
            state, "pkt-timeout-recovery", "tool_call_timeout", "opaque-undo-1b"
        )
    except ValueError as exc:
        print("\n=== Duplicate active packet_id refused ===")
        print(exc)

    rollback(state, "delta-1", "regression below declared threshold")
    print("\n=== After rollback(delta-1) ===")
    print(json.dumps(state, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
