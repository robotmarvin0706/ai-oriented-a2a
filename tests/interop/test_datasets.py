"""Public-corpus checks for datasets/ (non-normative, see docs/DATA_POLICY.md).

Run: pytest tests/interop/test_datasets.py -q
Requires: pytest, jsonschema
"""

import copy
import json
import re
from pathlib import Path

import pytest

try:
    import jsonschema
except ImportError:
    pytest.skip("jsonschema not installed", allow_module_level=True)

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "schema"
DATASETS = REPO / "datasets"

PACKET_SCHEMA = json.loads((SCHEMA_DIR / "experience-packet.schema.json").read_text())
RESULT_SCHEMA = json.loads((SCHEMA_DIR / "absorption-result.schema.json").read_text())

LEGAL_PREFIXES = ("synthetic:", "local_run:", "peer_absorbed:")
MAX_CHAIN_HOPS = 3

# Numeric uplift claims that synthetic data must not make:
# signed numbers (+0.2, -3), percentages, "0.72 -> 0.81" / "0.72→0.81", ΔSR / delta_sr.
UPLIFT_PATTERNS = [
    re.compile(r"(?<![\w.])[+-]\d"),
    re.compile(r"\d+(\.\d+)?\s*%"),
    re.compile(r"\d\s*(→|->)\s*\d"),
    re.compile(r"ΔSR|delta[_ ]?sr", re.IGNORECASE),
]

ALL_JSON = sorted(DATASETS.glob("**/*.json"))
PACKET_FILES = [p for p in ALL_JSON if "packets" in p.parts]
RESULT_FILES = [p for p in ALL_JSON if "absorption_examples" in p.parts]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _uplift_hits(texts):
    return [t for t in texts if any(p.search(t) for p in UPLIFT_PATTERNS)]


def _schema_errors(instance, schema):
    validator = jsonschema.Draft202012Validator(schema)
    return [e.message for e in validator.iter_errors(instance)]


def check_public_packet(packet):
    """Return a list of DATA_POLICY violations for a public-corpus packet (empty = ok)."""
    errors = _schema_errors(packet, PACKET_SCHEMA)

    prov = packet.get("provenance")
    if not isinstance(prov, dict) or not prov.get("source_ref"):
        errors.append("missing provenance.source_ref")
        source_ref = ""
    else:
        source_ref = prov["source_ref"]
        prefix = next((p for p in LEGAL_PREFIXES if source_ref.startswith(p)), None)
        if prefix is None:
            errors.append(f"illegal source_ref prefix: {source_ref!r}")
        elif not source_ref[len(prefix):].strip():
            errors.append(f"empty opaque part in source_ref: {source_ref!r}")
        chain = prov.get("chain", [])
        if len(chain) > MAX_CHAIN_HOPS:
            errors.append(f"chain has {len(chain)} hops (max {MAX_CHAIN_HOPS})")

    claim = packet.get("verification_claim")
    if not isinstance(claim, dict):
        claim = {}
        errors.append("missing verification_claim")
    for key in ("expected_effect", "replay_hint"):
        if not str(claim.get(key, "")).strip():
            errors.append(f"missing verification_claim.{key}")

    if source_ref.startswith("synthetic:"):
        texts = list(_strings(packet.get("content", {})))
        texts += [str(claim.get("expected_effect", "")), str(claim.get("replay_hint", ""))]
        for hit in _uplift_hits(texts):
            errors.append(f"numeric uplift in synthetic packet: {hit!r}")

    return errors


def check_absorption_example(result, packet_ids):
    """Return a list of violations for an Absorption Result shipped in a dataset."""
    errors = _schema_errors(result, RESULT_SCHEMA)
    if result.get("packet_ref") not in packet_ids:
        errors.append(f"packet_ref {result.get('packet_ref')!r} not in dataset")
    note = result.get("effect_summary", {}).get("note", "")
    for hit in _uplift_hits([note]):
        errors.append(f"numeric uplift in note: {hit!r}")
    return errors


def _dataset_root(path):
    for parent in path.parents:
        if parent.parent == DATASETS:
            return parent
    raise AssertionError(f"{path} is not inside a dataset folder")


# --- checks over the committed corpus ---------------------------------------


def test_datasets_readme_and_license_present():
    assert (DATASETS / "README.md").read_text().strip()
    assert "CC0" in (DATASETS / "LICENSE").read_text()


def test_at_least_five_packets():
    assert len(PACKET_FILES) >= 5


@pytest.mark.parametrize("path", ALL_JSON, ids=lambda p: str(p.relative_to(DATASETS)))
def test_every_json_parses(path):
    json.loads(path.read_text())


@pytest.mark.parametrize("path", PACKET_FILES, ids=lambda p: p.name)
def test_packet_meets_policy(path):
    packet = json.loads(path.read_text())
    assert check_public_packet(packet) == []
    assert packet["id"] == path.stem


def test_packet_ids_unique():
    ids = [json.loads(p.read_text())["id"] for p in PACKET_FILES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", RESULT_FILES, ids=lambda p: p.name)
def test_absorption_example_meets_policy(path):
    root = _dataset_root(path)
    packet_ids = {json.loads(p.read_text())["id"] for p in root.glob("packets/*.json")}
    result = json.loads(path.read_text())
    assert check_absorption_example(result, packet_ids) == []


# --- the checker rejects bad packets (in-memory, nothing bad on disk) -------

GOOD = {
    "id": "pkt-synth-fixture",
    "type": "tool_trajectory",
    "content": {"compressed_signal": "single retry on timeout"},
    "absorption": {"method": "retrieval", "target": "timeout_handling"},
    "provenance": {"source_ref": "synthetic:fixture"},
    "verification_claim": {
        "expected_effect": "recover from a single timeout without unbounded retries",
        "replay_hint": "one forced timeout, budget of 3 calls",
    },
}


def _variant(mutate):
    packet = copy.deepcopy(GOOD)
    mutate(packet)
    return packet


def test_good_fixture_passes():
    assert check_public_packet(GOOD) == []


def test_counter_example_marker_allowed():
    packet = _variant(lambda p: p["content"].update(counter_example=True))
    assert check_public_packet(packet) == []


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda p: p.pop("provenance"), "missing provenance.source_ref"),
        (lambda p: p["provenance"].update(source_ref="peer:agent-alpha"), "illegal source_ref prefix"),
        (lambda p: p["provenance"].update(source_ref="local:run-1"), "illegal source_ref prefix"),
        (lambda p: p["provenance"].update(source_ref="synthetic:"), "empty opaque part"),
        (lambda p: p.pop("verification_claim"), "missing verification_claim"),
        (lambda p: p["verification_claim"].pop("expected_effect"), "missing verification_claim.expected_effect"),
        (lambda p: p["verification_claim"].pop("replay_hint"), "missing verification_claim.replay_hint"),
        (lambda p: p["verification_claim"].update(replay_hint="   "), "missing verification_claim.replay_hint"),
        (
            lambda p: p["provenance"].update(chain=[f"synthetic:hop-{i}" for i in range(4)]),
            "chain has 4 hops",
        ),
        (lambda p: p["verification_claim"].update(expected_effect="SR +0.2"), "numeric uplift"),
        (lambda p: p["content"].update(note="SR 0.72→0.81"), "numeric uplift"),
        (lambda p: p["content"].update(note="0.72 -> 0.81"), "numeric uplift"),
        (lambda p: p["content"].update(note="success up 12%"), "numeric uplift"),
        (lambda p: p["content"].update(note="positive ΔSR"), "numeric uplift"),
    ],
)
def test_checker_rejects(mutate, expected):
    errors = check_public_packet(_variant(mutate))
    assert any(expected in e for e in errors), errors


def test_hyphenated_ids_are_not_uplift():
    packet = _variant(lambda p: p["content"].update(ref="pkt-synth-timeout-001", note="timeout >= 30s"))
    assert check_public_packet(packet) == []


def test_absorption_checker_rejects_unknown_ref_and_uplift():
    bad = {
        "packet_ref": "pkt-not-here",
        "status": "absorbed",
        "effect_summary": {"note": "SR 0.72→0.81"},
    }
    errors = check_absorption_example(bad, {"pkt-synth-fixture"})
    assert any("not in dataset" in e for e in errors)
    assert any("numeric uplift" in e for e in errors)


# --- the wire schema is unchanged -------------------------------------------


def test_schema_keeps_provenance_optional():
    packet = _variant(lambda p: (p.pop("provenance"), p.pop("verification_claim")))
    jsonschema.validate(packet, PACKET_SCHEMA)
    assert "provenance" not in PACKET_SCHEMA["required"]
    assert "verification_claim" not in PACKET_SCHEMA["required"]
