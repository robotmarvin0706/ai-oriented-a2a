"""Experiment-path hard gate for Absorption Result (non-normative).

Wire schema keeps effect_summary OPTIONAL. The experiment path (Receiver.absorb
and enforce_effect_summary_or_reject) requires a compliant summary or converts
the result to rejected + rollback.
"""

from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from minimal_dual_end import (  # noqa: E402
    Receiver,
    ReceiverState,
    enforce_effect_summary_or_reject,
    effect_summary_is_compliant,
)


def test_missing_effect_summary_forces_reject_and_rollback():
    state = ReceiverState()
    state.prompt_prefix = "injected recovery"
    state.retrieval_store["pkt-1"] = {"hint": "backoff"}

    incomplete = {
        "packet_ref": "pkt-1",
        "status": "absorbed",
        # deliberately omit effect_summary
    }

    out = enforce_effect_summary_or_reject(
        state,
        incomplete,
        isolation_n=3,
        drop_threshold=0.10,
    )

    assert out["status"] == "rejected"
    assert "effect_summary" in out
    assert "non-compliant" in out["reason"] or "rolled back" in out["reason"]
    # state rolled back
    assert state.prompt_prefix == ""
    assert "pkt-1" not in state.retrieval_store
    ok, _ = effect_summary_is_compliant(out)
    assert ok


def test_compliant_summary_passes_gate():
    state = ReceiverState()
    compliant = {
        "packet_ref": "pkt-ok",
        "status": "absorbed",
        "effect_summary": {
            "note": "isolation passed",
            "metrics": {
                "baseline_sr": 0.6,
                "regression_sr": 0.85,
                "isolation_sr": 0.9,
            },
        },
    }
    out = enforce_effect_summary_or_reject(
        state,
        compliant,
        isolation_n=3,
        drop_threshold=0.10,
    )
    assert out["status"] == "absorbed"
    assert out["effect_summary"]["metrics"]["declared_N"] == 3
    assert out["effect_summary"]["metrics"]["rollback_triggered"] is False


def test_receiver_absorb_produces_compliant_or_rejected():
    recv = Receiver(isolation_n=3)
    packet = {
        "id": "pkt-gate-1",
        "type": "failure_mode",
        "content": {"trigger": "timeout", "compressed_signal": "backoff"},
        "absorption": {"method": "prompt_injection", "hint": "prepend backoff"},
    }
    import random
    result = recv.absorb(packet, random.Random(42))
    assert result["status"] in ("absorbed", "rejected", "isolated")
    if result["status"] != "absorbed":
        assert "reason" in result
    # experiment path always emits a compliant summary after the gate
    ok, why = effect_summary_is_compliant(result)
    assert ok, why
