"""Audit P1-8: a version string long enough to break int() must be a
version problem (HTTP 200 + unsupported_version), never a 500."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _p in (str(REPO), str(REPO / "second_impl")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_oriented_a2a import check_incoming  # noqa: E402
from ai_oriented_a2a.protocol import supported_version, version_rejection  # noqa: E402
from second_impl import a2a_core  # noqa: E402

LONG = "9" * 5000 + ".0"


def _packet(version):
    return {
        "id": "pkt-version-limit",
        "type": "failure_mode",
        "content": {"compressed_signal": "retry with backoff"},
        "absorption": {"method": "retrieval"},
        "version": version,
    }


def test_supported_version_does_not_raise():
    assert supported_version(LONG) is False


def test_check_incoming_returns_unsupported_version():
    rejected = check_incoming(_packet(LONG))
    assert rejected is not None
    assert rejected["status"] == "rejected"
    assert "unsupported_version" in rejected["reason"]
    # The reason must not echo 5000 characters back to the peer.
    assert len(rejected["reason"]) < 200


def test_version_rejection_keeps_the_reason_short():
    reason = version_rejection(_packet(LONG))
    assert reason is not None and len(reason) < 200


def test_second_impl_does_not_raise():
    reason = a2a_core.version_problem(_packet(LONG))
    assert reason is not None and "unsupported_version" in reason and len(reason) < 200


def test_short_unsupported_version_still_names_the_value():
    assert "0.5" in version_rejection(_packet("0.5"))
    assert "0.5" in a2a_core.version_problem(_packet("0.5"))
