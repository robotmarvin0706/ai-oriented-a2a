"""ISO-8601 timestamp schema pattern and the bounded replay cache.

Covers KNOWN_GAPS items closed in 0.3.2: string timestamps must be
ISO-8601 with a timezone (schema pattern), the replay set is trimmed by
age rather than arbitrarily, and a nonce is a duplicate whatever its
timestamp.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_oriented_a2a import check_replay, parse_timestamp  # noqa: E402
from ai_oriented_a2a import protocol  # noqa: E402
from second_impl import a2a_core as core  # noqa: E402

jsonschema = pytest.importorskip("jsonschema")
from ai_oriented_a2a import validate  # noqa: E402

EXAMPLES = REPO / "examples"
SAMPLES = [
    ("absorption-result", "absorption-result-reject.json"),
    ("agent-profile", "agent-profile.json"),
    ("experience-packet", "experience-packet.json"),
    ("negotiation", "negotiation-propose.json"),
    ("negotiation", "negotiation-counter.json"),
    ("negotiation", "negotiation-accept.json"),
    ("negotiation", "negotiation-reject.json"),
]

VALID_STRINGS = [
    "2026-11-14T08:00:00Z",
    "2026-11-14T08:00:00+08:00",
    "2026-11-14T08:00:00-05:30",
    "2026-11-14T08:00:00.123Z",
    "2026-11-14T08:00:00.123456+00:00",
    "2026-12-31T23:59:59Z",
]

INVALID_STRINGS = [
    "1731480000",  # numeric string
    "2026-11-14T08:00:00",  # no timezone
    "2026-11-14",  # date only
    "2026-11-14 08:00:00Z",  # space separator
    "2026-11-14t08:00:00z",  # lowercase
    "2026-11-14T08:00:00+0800",  # offset without colon
    "2026-11-14T08:00:00.1Z",  # 1-digit fraction
    "2026-11-14T08:00:00.123456789Z",  # 9-digit fraction
    "2026-13-01T00:00:00Z",  # month 13
    "2026-11-14T24:00:00Z",  # hour 24
    "not a timestamp",
    "",
]


def _sample(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _with_ts(kind: str, name: str, ts) -> dict:
    msg = _sample(name)
    validate(msg, kind)  # sample is valid before the change
    msg["timestamp"] = ts
    return msg


@pytest.mark.parametrize("kind,name", SAMPLES)
@pytest.mark.parametrize("ts", VALID_STRINGS + [1731480000, 1731480000.5])
def test_schema_accepts_iso_with_timezone_and_numbers(kind, name, ts):
    validate(_with_ts(kind, name, ts), kind)


@pytest.mark.parametrize("kind,name", SAMPLES)
@pytest.mark.parametrize("ts", INVALID_STRINGS)
def test_schema_rejects_other_strings(kind, name, ts):
    with pytest.raises(jsonschema.ValidationError):
        validate(_with_ts(kind, name, ts), kind)


def test_all_seven_timestamp_spots_carry_the_pattern():
    spots = 0
    patterns = set()
    for path in sorted((REPO / "schema").glob("*.schema.json")):
        def walk(node):
            nonlocal spots
            if isinstance(node, dict):
                ts = node.get("properties", {}).get("timestamp") if isinstance(node.get("properties"), dict) else None
                if ts is not None:
                    spots += 1
                    string_branch = [b for b in ts["oneOf"] if b.get("type") == "string"][0]
                    patterns.add(string_branch["pattern"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(json.loads(path.read_text(encoding="utf-8")))
    assert spots == 7
    assert len(patterns) == 1


@pytest.mark.parametrize("ts", VALID_STRINGS)
def test_schema_valid_strings_parse_in_both_implementations(ts):
    ref = parse_timestamp(ts)
    other = core.parse_timestamp(ts)
    assert ref is not None and other is not None
    assert ref == other


def test_naive_string_still_parsed_as_utc_for_compatibility():
    # Schema rejects it (see INVALID_STRINGS); the lenient parser keeps it.
    assert parse_timestamp("2026-11-14T08:00:00") == parse_timestamp("2026-11-14T08:00:00Z")


# ---------------------------------------------------------------------------
# Replay cache
# ---------------------------------------------------------------------------


def _msg(nonce: str, ts) -> dict:
    return {"nonce": nonce, "timestamp": ts}


def test_duplicate_nonce_in_window_rejected():
    seen: set = set()
    now = int(time.time())
    assert check_replay(_msg("n1", now), seen, 300) is None
    assert check_replay(_msg("n1", now), seen, 300) == "replay_or_stale: duplicate nonce"


def test_same_nonce_with_new_timestamp_rejected():
    seen: set = set()
    now = int(time.time())
    assert check_replay(_msg("n1", now), seen, 300) is None
    assert check_replay(_msg("n1", now + 5), seen, 300) == "replay_or_stale: duplicate nonce"
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3))
    assert check_replay(_msg("n1", iso), seen, 300) == "replay_or_stale: duplicate nonce"


def test_seen_stays_a_plain_set_of_string_keys():
    seen: set = set()
    now = int(time.time())
    check_replay(_msg("n1", now), seen, 300)
    assert isinstance(seen, set)
    assert seen == {("n1", str(now))}


def test_eviction_drops_expired_keys_first():
    window = 300
    now = int(time.time())
    seen = {(f"old{i}", str(now - 10_000 - i)) for i in range(300)}
    seen |= {(f"live{i}", str(now - i % 200)) for i in range(protocol.SEEN_MAX - 300)}
    assert len(seen) == protocol.SEEN_MAX
    assert check_replay(_msg("fresh", now), seen, window) is None
    assert not [k for k in seen if k[0].startswith("old")]
    # Only expired keys were needed: every live key survives.
    assert len([k for k in seen if k[0].startswith("live")]) == protocol.SEEN_MAX - 300
    assert ("fresh", str(now)) in seen


def test_eviction_then_drops_oldest_live_keys():
    window = 3600
    now = int(time.time())
    seen = {(f"n{i:04d}", str(now - 2000 + i)) for i in range(protocol.SEEN_MAX)}
    assert check_replay(_msg("fresh", now), seen, window) is None
    assert len(seen) == protocol.SEEN_TRIM_TO
    assert ("fresh", str(now)) in seen
    kept_ts = sorted(int(k[1]) for k in seen if k[0] != "fresh")
    # The newest SEEN_TRIM_TO - 1 timestamps were kept, the oldest dropped.
    assert kept_ts[0] == now - 2000 + (protocol.SEEN_MAX + 1 - protocol.SEEN_TRIM_TO)
    # A nonce that is still cached is rejected after the trim.
    assert check_replay(_msg(f"n{protocol.SEEN_MAX - 1:04d}", now), seen, window) == (
        "replay_or_stale: duplicate nonce"
    )


def test_constants_documented_values():
    assert protocol.SEEN_MAX == 1000
    assert protocol.SEEN_TRIM_TO == 800
