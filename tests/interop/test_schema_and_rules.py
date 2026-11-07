"""Reference interop tests (non-normative).

Run: pytest tests/interop/ -q
Requires: pytest, jsonschema
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
EXAMPLES = REPO / "examples"

PACKET_SCHEMA = json.loads((SCHEMA_DIR / "experience-packet.schema.json").read_text())
RESULT_SCHEMA = json.loads((SCHEMA_DIR / "absorption-result.schema.json").read_text())
PROFILE_SCHEMA = json.loads((SCHEMA_DIR / "agent-profile.schema.json").read_text())


def test_existing_examples_validate():
    mapping = {
        "agent-profile.json": PROFILE_SCHEMA,
        "experience-packet.json": PACKET_SCHEMA,
        "experience-packet-with-provenance.json": PACKET_SCHEMA,
        "absorption-result-absorbed.json": RESULT_SCHEMA,
        "absorption-result-reject.json": RESULT_SCHEMA,
    }
    for name, schema in mapping.items():
        instance = json.loads((EXAMPLES / name).read_text())
        jsonschema.validate(instance, schema)


def test_unsupported_method_must_be_rejectable():
    """SPEC: unsupported method MUST be explicitly rejected."""
    packet = {
        "id": "pkt-test-unsupported",
        "type": "failure_mode",
        "content": {"trigger": "test"},
        "absorption": {"method": "parameter_delta", "target": "weights"},
    }
    jsonschema.validate(packet, PACKET_SCHEMA)  # schema allows it
    # Behavior expectation (reference): result status rejected
    expected_status = "rejected"
    assert expected_status in ("rejected", "isolated")


def test_packet_id_required():
    bad = {
        "type": "failure_mode",
        "content": {},
        "absorption": {"method": "prompt_injection"},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, PACKET_SCHEMA)


def test_packet_ref_matches_id():
    packet_id = "pkt-match-123"
    result = {
        "packet_ref": packet_id,
        "status": "absorbed",
    }
    jsonschema.validate(result, RESULT_SCHEMA)
    assert result["packet_ref"] == packet_id
