"""Absorption Result hard gate: normative schema tests + reference experiment path.

Normative (SPEC §3.1, schema/absorption-result.schema.json):
  status=absorbed requires effect_summary with a non-empty note.
  status=rejected or isolated requires reason.
  effect_summary is optional for rejected and isolated.
  Metric keys remain optional on the wire.

The experiment-path tests below continue to exercise
examples/minimal_dual_end.py (Receiver.absorb and
enforce_effect_summary_or_reject), which is stricter than the wire
gate and still converts a missing summary to rejected + rollback.
Those tests are reference-implementation coverage, not additional
wire requirements.
"""

import json
from pathlib import Path
import sys

import pytest

try:
    import jsonschema
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:
    pytest.skip("jsonschema/referencing not installed", allow_module_level=True)

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
SCHEMA_DIR = REPO / "schema"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from minimal_dual_end import (  # noqa: E402
    Receiver,
    ReceiverState,
    enforce_effect_summary_or_reject,
    effect_summary_is_compliant,
)


def _local_registry():
    resources = []
    for path in SCHEMA_DIR.glob("*.schema.json"):
        contents = json.loads(path.read_text())
        resource = Resource.from_contents(contents)
        resources.append((path.name, resource))
        schema_id = contents.get("$id")
        if schema_id:
            resources.append((schema_id, resource))
    return Registry().with_resources(resources)


REGISTRY = _local_registry()
RESULT_SCHEMA = json.loads((SCHEMA_DIR / "absorption-result.schema.json").read_text())
VALIDATOR = Draft202012Validator(RESULT_SCHEMA, registry=REGISTRY)


# ---------------------------------------------------------------------------
# Normative wire-schema tests (SPEC §3.1 hard gate)
# ---------------------------------------------------------------------------


def test_absorbed_without_effect_summary_is_invalid():
    result = {"packet_ref": "pkt-1", "status": "absorbed"}
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(result)


def test_absorbed_with_empty_note_is_invalid():
    result = {
        "packet_ref": "pkt-1",
        "status": "absorbed",
        "effect_summary": {"note": ""},
    }
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(result)


def test_absorbed_with_whitespace_only_note_is_invalid():
    result = {
        "packet_ref": "pkt-1",
        "status": "absorbed",
        "effect_summary": {"note": "   "},
    }
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(result)


def test_absorbed_with_nonempty_note_is_valid_without_metrics():
    result = {
        "packet_ref": "pkt-1",
        "status": "absorbed",
        "effect_summary": {"note": "local regression passed, SR 0.72→0.81"},
    }
    VALIDATOR.validate(result)


def test_absorbed_missing_note_key_is_invalid():
    result = {
        "packet_ref": "pkt-1",
        "status": "absorbed",
        "effect_summary": {"metrics": {"baseline_sr": 0.5}},
    }
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(result)


def test_rejected_without_reason_is_invalid():
    result = {"packet_ref": "pkt-1", "status": "rejected"}
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(result)


def test_isolated_without_reason_is_invalid():
    result = {"packet_ref": "pkt-1", "status": "isolated"}
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(result)


def test_rejected_reason_only_effect_summary_optional():
    result = {
        "packet_ref": "pkt-1",
        "status": "rejected",
        "reason": "unsupported method: parameter_delta",
    }
    VALIDATOR.validate(result)


def test_isolated_reason_only_effect_summary_optional():
    result = {
        "packet_ref": "pkt-1",
        "status": "isolated",
        "reason": "held for inspection",
    }
    VALIDATOR.validate(result)


def test_metrics_may_include_boolean_values():
    """Non-breaking relaxation: reference impl emits rollback_triggered as bool."""
    result = {
        "packet_ref": "pkt-1",
        "status": "absorbed",
        "effect_summary": {
            "note": "isolation passed",
            "metrics": {
                "baseline_sr": 0.6,
                "rollback_triggered": False,
                "rollback_action": "none",
            },
        },
    }
    VALIDATOR.validate(result)


def test_all_absorbed_examples_have_nonempty_note():
    for name in (
        "absorption-result-absorbed.json",
        "absorption-result-with-effect.json",
    ):
        instance = json.loads((EXAMPLES / name).read_text())
        assert instance["status"] == "absorbed"
        note = instance["effect_summary"]["note"]
        assert isinstance(note, str) and note.strip()
        VALIDATOR.validate(instance)


# ---------------------------------------------------------------------------
# Reference experiment-path tests (stricter than the wire gate)
# ---------------------------------------------------------------------------


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
    # rejected result is valid on the wire (reason MUST, summary OPTIONAL-but-present)
    VALIDATOR.validate(
        {
            "packet_ref": out["packet_ref"],
            "status": out["status"],
            "reason": out["reason"],
            "effect_summary": {
                "note": out["effect_summary"]["note"],
                # metrics may contain booleans in the reference impl; only note is required
            },
        }
    )


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
    VALIDATOR.validate(
        {
            "packet_ref": out["packet_ref"],
            "status": out["status"],
            "effect_summary": {
                "note": out["effect_summary"]["note"],
                "metrics": {
                    "baseline_sr": 0.6,
                    "regression_sr": 0.85,
                    "declared_N": 3,
                },
            },
        }
    )


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
    # wire-level: absorbed must have a non-empty note; rejected/isolated a reason
    assert result.get("effect_summary", {}).get("note", "").strip()
    if result["status"] in ("rejected", "isolated"):
        assert result.get("reason")
