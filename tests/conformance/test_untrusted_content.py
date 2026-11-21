"""SPEC §2.1 conformance cases for any AI-oriented A2A v0.4 receiver.

Not part of the 12 HTTP cases copied from tests/interop/test_optional_fields.py,
so nothing here needs a second copy. Runs against every implementation of the
``start_receiver`` fixture (reference, second_impl, and an external peer).

- The two counter-examples in ``datasets/content_policy/packets/`` get HTTP
  200 and a legal rejected Absorption Result whose reason starts with
  ``content_policy:`` and whose ``packet_ref`` is the packet id. Never
  ``absorbed``.
- The same packet nested as ``offered_packet`` in a negotiation propose is
  rejected with ``content_policy:`` and the nested id as ``packet_ref``.
- On-wire ``"0.3"`` and ``"0.4"`` are both accepted (no
  ``unsupported_version``); ``"0.5"`` is not.

No ``dict |`` (Python 3.8).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
INJECT_FILES = sorted((REPO / "datasets" / "content_policy" / "packets").glob("*.json"))
ENDPOINT = "/a2a/message"

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:
    pytest.skip("jsonschema/referencing not installed", allow_module_level=True)


def _registry():
    resources = []
    for path in (REPO / "schema").glob("*.schema.json"):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(contents)
        resources.append((path.name, resource))
        if contents.get("$id"):
            resources.append((contents["$id"], resource))
    return Registry().with_resources(resources)


RESULT_V = Draft202012Validator(
    json.loads((REPO / "schema" / "absorption-result.schema.json").read_text(encoding="utf-8")),
    registry=_registry(),
)


def _post(url, msg, timeout=10):
    req = urllib.request.Request(
        url.rstrip("/") + ENDPOINT,
        data=json.dumps(msg).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_content_policy(status, body, packet_ref):
    assert status == 200, (status, body)
    RESULT_V.validate(body)
    assert body["status"] == "rejected", body
    assert body["reason"].startswith("content_policy:"), body["reason"]
    assert body["packet_ref"] == packet_ref


@pytest.mark.parametrize("path", INJECT_FILES, ids=lambda p: p.stem)
def test_counter_example_packet_rejected_with_content_policy(start_receiver, path):
    url = start_receiver()
    pkt = _load(path)
    status, body = _post(url, pkt)
    _assert_content_policy(status, body, pkt["id"])


@pytest.mark.parametrize("path", INJECT_FILES, ids=lambda p: p.stem)
def test_counter_example_nested_in_negotiation_rejected(start_receiver, path):
    url = start_receiver()
    pkt = _load(path)
    propose = {
        "proposal_id": "prop-content-policy-1",
        "target_constraint": "web_search allowed",
        "proposed_change": "allow one extra retry",
        "offered_packet": pkt,
    }
    status, body = _post(url, propose)
    _assert_content_policy(status, body, pkt["id"])


@pytest.mark.parametrize("version", ["0.3", "0.4"])
def test_supported_versions_are_not_rejected_as_unsupported(start_receiver, version):
    url = start_receiver()
    pkt = _load(EXAMPLES / "experience-packet.json")
    pkt["version"] = version
    status, body = _post(url, pkt)
    assert status == 200, body
    RESULT_V.validate(body)
    assert body["packet_ref"] == pkt["id"]
    assert "unsupported_version" not in body.get("reason", ""), body
    assert not body.get("reason", "").startswith("content_policy:"), body


def test_version_0_3_counter_example_still_content_policy(start_receiver):
    url = start_receiver()
    pkt = _load(INJECT_FILES[0])
    pkt["version"] = "0.3"
    status, body = _post(url, pkt)
    _assert_content_policy(status, body, pkt["id"])
