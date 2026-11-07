"""Additional boundary tests (non-normative).

Covers nested packet id, effect_summary presence expectations, and basic rejection notes.
Uses a local schema registry so relative $ref resolution does not require network.
"""

import json
from pathlib import Path

import pytest

try:
    import jsonschema
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:
    pytest.skip("jsonschema/referencing not installed", allow_module_level=True)

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "schema"


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
    validator = Draft202012Validator(NEG_SCHEMA, registry=REGISTRY)
    validator.validate(propose)
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
    Draft202012Validator(RESULT_SCHEMA, registry=REGISTRY).validate(result)


def test_rejected_requires_reason_in_practice():
    result = {
        "packet_ref": "pkt-bad",
        "status": "rejected",
        "reason": "unsupported method: parameter_delta",
    }
    Draft202012Validator(RESULT_SCHEMA, registry=REGISTRY).validate(result)
    assert "reason" in result


def test_missing_effect_summary_still_valid_schema():
    result = {"packet_ref": "pkt-1", "status": "absorbed"}
    Draft202012Validator(RESULT_SCHEMA, registry=REGISTRY).validate(result)  # optional in wire
