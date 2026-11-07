"""Additional boundary tests (non-normative).

Covers nested packet id, effect_summary presence expectations, and basic replay-like rejection notes.
"""

import json
from pathlib import Path

import pytest

try:
    import jsonschema
except ImportError:
    pytest.skip("jsonschema not installed", allow_module_level=True)

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "schema"

PACKET_SCHEMA = json.loads((SCHEMA_DIR / "experience-packet.schema.json").read_text())
RESULT_SCHEMA = json.loads((SCHEMA_DIR / "absorption-result.schema.json").read_text())
NEG_SCHEMA = json.loads((SCHEMA_DIR / "negotiation.schema.json").read_text())


def test_nested_packet_has_own_id():
    nested = {
        "id": "pkt-nested-1",
        "type": "failure_mode",
        "content": {"trigger": "nested"},
        "absorption": {"method": "prompt_injection"},
    }
    propose = {
        "proposal_id": "prop-1",
        "target_constraint": "timeout_handling",
        "proposed_change": "allow backoff",
        "offered_packet": nested,
    }
    jsonschema.validate(propose, NEG_SCHEMA)
    assert propose["offered_packet"]["id"] == "pkt-nested-1"


def test_effect_summary_optional_but_structured_when_present():
    result = {
        "packet_ref": "pkt-1",
        "status": "absorbed",
        "effect_summary": {
            "note": "regression passed",
            "metrics": {"baseline_sr": 0.5, "regression_sr": 0.8},
        },
    }
    jsonschema.validate(result, RESULT_SCHEMA)


def test_rejected_requires_reason_in_practice():
    result = {
        "packet_ref": "pkt-bad",
        "status": "rejected",
        "reason": "unsupported method: parameter_delta",
    }
    jsonschema.validate(result, RESULT_SCHEMA)
    assert "reason" in result


def test_missing_effect_summary_still_valid_schema():
    result = {"packet_ref": "pkt-1", "status": "absorbed"}
    jsonschema.validate(result, RESULT_SCHEMA)  # optional in wire
