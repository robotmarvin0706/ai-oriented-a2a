"""Protocol core for the independent second implementation (v0.3).

Written from SPEC.md, docs/TRANSPORT.md, docs/VERSIONING.md and
docs/SECURITY.md. Standard library only; JSON Schema validation uses the
optional ``jsonschema`` package when it is installed.

This module MUST NOT import ``ai_oriented_a2a`` or anything under
``examples/``. tests/interop/test_second_impl.py enforces that.

Receiver check order (``check_incoming``):

1. version   -> ``unsupported_version: received <value>``
2. hard gate -> ``hard-gate violation: ...`` (Absorption Results only)
3. schema    -> ``schema_invalid: ...`` (on by default)
4. signature -> ``signature verification failed`` (only with a key)
5. replay    -> ``replay_or_stale: ...`` (only with a window)

Every failure is a valid Absorption Result with ``status=rejected``, a
non-empty ``packet_ref`` and a non-empty ``reason``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

UNKNOWN_REF = "unknown"
SUPPORTED_MAJOR_MINOR = (0, 3)
WIRE_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")
CLIENT_TIMEOUT_SECONDS = 30
MUST_METHODS = ("prompt_injection", "retrieval")
SIG_ALG = "hmac-sha256"

SCHEMA_FILES = {
    "absorption-result": "absorption-result.schema.json",
    "agent-profile": "agent-profile.schema.json",
    "experience-packet": "experience-packet.schema.json",
    "negotiation": "negotiation.schema.json",
}

# Reason strings and prefixes (docs/TRANSPORT.md, "Reserved reason prefixes").
REASON_UNSUPPORTED_VERSION = "unsupported_version"
REASON_REPLAY = "replay_or_stale"
REASON_SCHEMA = "schema_invalid"
REASON_NEGOTIATION = "negotiation_unsupported"
REASON_SIGNATURE = "signature verification failed"
REASON_INVALID_JSON = "invalid json"
ISOLATED_REASON = "isolated: no local regression available"

Message = Dict[str, Any]


# ---------------------------------------------------------------------------
# References and legal rejections
# ---------------------------------------------------------------------------


def _usable(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def ref_of(msg: Any) -> str:
    """packet_ref for a reply: ``id``, then ``packet_ref``, then the nested
    ``offered_packet.id`` of a negotiation message, else ``"unknown"``."""
    if not isinstance(msg, dict):
        return UNKNOWN_REF
    for key in ("id", "packet_ref"):
        if _usable(msg.get(key)):
            return msg[key]
    offered = msg.get("offered_packet")
    if isinstance(offered, dict) and _usable(offered.get("id")):
        return offered["id"]
    return UNKNOWN_REF


def rejected(msg: Any, reason: str) -> Message:
    """A legal rejected Absorption Result."""
    if not _usable(reason):
        raise ValueError("reason must be a non-empty, non-whitespace string")
    return {"packet_ref": ref_of(msg), "status": "rejected", "reason": reason}


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def version_problem(msg: Message) -> Optional[str]:
    """None if ``version`` is absent or on the 0.3 line, else a reason.

    Checked before anything else: ``"v0.3"`` and non-strings are unknown
    versions, not schema errors.
    """
    if "version" not in msg:
        return None
    value = msg["version"]
    if isinstance(value, str) and WIRE_VERSION_RE.match(value):
        major, minor = value.split(".")[:2]
        if (int(major), int(minor)) == SUPPORTED_MAJOR_MINOR:
            return None
    shown = value if isinstance(value, str) else json.dumps(value)
    return f"{REASON_UNSUPPORTED_VERSION}: received {shown}"


# ---------------------------------------------------------------------------
# Signature (HMAC-SHA256 over canonical JSON without "signature")
# ---------------------------------------------------------------------------


def canonical_bytes(msg: Message) -> bytes:
    body = {k: v for k, v in msg.items() if k != "signature"}
    text = json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return text.encode("utf-8")


def _digest(msg: Message, key: str) -> str:
    return hmac.new(key.encode("utf-8"), canonical_bytes(msg), hashlib.sha256).hexdigest()


def sign(msg: Message, key: str) -> Message:
    out = dict(msg)
    out["signature"] = {"alg": SIG_ALG, "value": _digest(msg, key)}
    return out


def verify(msg: Message, key: str) -> bool:
    sig = msg.get("signature")
    if not isinstance(sig, dict) or sig.get("alg") != SIG_ALG:
        return False
    value = sig.get("value")
    if not isinstance(value, str):
        return False
    return hmac.compare_digest(_digest(msg, key), value)


# ---------------------------------------------------------------------------
# Replay window
# ---------------------------------------------------------------------------


def parse_timestamp(value: Any) -> Optional[float]:
    """Unix seconds (number) or ISO-8601 string (naive = UTC, ``Z`` ok)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


class ReplayWindow:
    """Sliding window of seen nonces. Thread-safe.

    Keyed on the nonce alone. Entries expire ``seconds`` after they were
    accepted, so memory stays bounded by the message rate.
    """

    def __init__(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("replay window must be positive")
        self.seconds = float(seconds)
        self._expiry: Dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, msg: Message, now: Optional[float] = None) -> Optional[str]:
        now = time.time() if now is None else now
        nonce = msg.get("nonce")
        if "nonce" not in msg or "timestamp" not in msg:
            return f"{REASON_REPLAY}: nonce and timestamp required when replay window is enabled"
        if not _usable(nonce):
            return f"{REASON_REPLAY}: missing or empty nonce"
        ts = parse_timestamp(msg.get("timestamp"))
        if ts is None:
            return f"{REASON_REPLAY}: unparsable timestamp"
        if abs(now - ts) > self.seconds:
            return f"{REASON_REPLAY}: timestamp outside window"
        with self._lock:
            self._expiry = {n: t for n, t in self._expiry.items() if t > now}
            if nonce in self._expiry:
                return f"{REASON_REPLAY}: duplicate nonce"
            self._expiry[nonce] = now + self.seconds
        return None


def fresh_replay_fields(msg: Message) -> Message:
    """Return a copy with a new ``nonce`` and the current integer ``timestamp``."""
    out = dict(msg)
    out["nonce"] = "nonce-" + uuid.uuid4().hex
    out["timestamp"] = int(time.time())
    return out


# ---------------------------------------------------------------------------
# Message kind, hard gate, schema
# ---------------------------------------------------------------------------


def kind_of(msg: Message) -> str:
    if "packet_ref" in msg or ("status" in msg and "proposal_id" not in msg):
        return "absorption-result"
    if "proposal_id" in msg:
        return "negotiation"
    if "constraints" in msg or "improvement_prefs" in msg or "shareable_types" in msg:
        return "agent-profile"
    return "experience-packet"


def gate_problem(result: Message) -> Optional[str]:
    """SPEC §3.1 hard gate for an Absorption Result."""
    if result.get("status") != "absorbed":
        return None
    summary = result.get("effect_summary")
    note = summary.get("note") if isinstance(summary, dict) else None
    if not _usable(note):
        return "hard-gate violation: absorbed result missing non-empty effect_summary.note"
    if result.get("packet_ref") == UNKNOWN_REF:
        return "hard-gate violation: reserved packet_ref 'unknown' cannot be absorbed"
    return None


def schema_dir() -> Path:
    """``$A2A_SCHEMA_DIR`` if set, else the repository's ``schema/``."""
    override = os.environ.get("A2A_SCHEMA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "schema"


_VALIDATORS: Dict[Tuple[str, str], Any] = {}
_WARNED = [False]


def jsonschema_available() -> bool:
    try:
        import jsonschema  # noqa: F401
        import referencing  # noqa: F401
    except ImportError:
        return False
    return True


def _validator(kind: str) -> Any:
    directory = schema_dir()
    key = (str(directory), kind)
    if key not in _VALIDATORS:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource

        resources = []
        for path in sorted(directory.glob("*.schema.json")):
            contents = json.loads(path.read_text(encoding="utf-8"))
            resource = Resource.from_contents(contents)
            resources.append((path.name, resource))
            if "$id" in contents:
                resources.append((contents["$id"], resource))
        schema_path = directory / SCHEMA_FILES[kind]
        if not schema_path.is_file():
            raise FileNotFoundError(f"schema not found: {schema_path} (set A2A_SCHEMA_DIR)")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        _VALIDATORS[key] = Draft202012Validator(schema, registry=Registry().with_resources(resources))
    return _VALIDATORS[key]


def schema_problem(msg: Message, kind: Optional[str] = None) -> Optional[str]:
    """``schema_invalid: ...`` if ``msg`` fails its published schema.

    Returns None (and warns once on stderr) when jsonschema is not installed.
    """
    if not jsonschema_available():
        if not _WARNED[0]:
            print("second_impl: jsonschema not installed; schema check skipped", file=sys.stderr)
            _WARNED[0] = True
        return None
    errors = sorted(_validator(kind or kind_of(msg)).iter_errors(msg), key=lambda e: list(e.path))
    if not errors:
        return None
    err = errors[0]
    where = "/".join(str(p) for p in err.path) or "(root)"
    return f"{REASON_SCHEMA}: {where}: {err.message[:160]}"


# ---------------------------------------------------------------------------
# Receiver-side combined check
# ---------------------------------------------------------------------------


def check_incoming(
    msg: Message,
    *,
    sign_key: Optional[str] = None,
    replay: Optional[ReplayWindow] = None,
    schema: bool = True,
) -> Optional[Message]:
    """Rejected Absorption Result if a check fails, else None.

    Order: version, hard gate, schema, signature, replay. Replay runs last
    so an unsigned or forged message cannot consume a nonce.
    """
    reason = version_problem(msg)
    if reason is None and kind_of(msg) == "absorption-result":
        reason = gate_problem(msg)
    if reason is None and schema:
        reason = schema_problem(msg)
    if reason is None and sign_key and not verify(msg, sign_key):
        reason = REASON_SIGNATURE
    if reason is None and replay is not None:
        reason = replay.check(msg)
    return rejected(msg, reason) if reason else None


# ---------------------------------------------------------------------------
# Sender-side checks
# ---------------------------------------------------------------------------


def check_outgoing(msg: Any, *, schema: bool = True) -> Optional[str]:
    """Problem with a message we are about to send, else None."""
    if not isinstance(msg, dict):
        return "message must be a JSON object"
    reason = version_problem(msg)
    if reason is None and kind_of(msg) == "absorption-result":
        reason = gate_problem(msg)
    if reason is None and schema:
        reason = schema_problem(msg)
    return reason


def is_absorption_result(body: Any) -> bool:
    return isinstance(body, dict) and "packet_ref" in body and "status" in body


def check_reply(
    sent: Message,
    body: Any,
    *,
    sign_key: Optional[str] = None,
    schema: bool = True,
) -> Optional[str]:
    """Problem with a peer's reply, else None.

    Replies are never checked against a replay window: they carry no
    nonce or timestamp.
    """
    if not isinstance(body, dict):
        return "reply is not a JSON object"
    if sign_key and "signature" in body and not verify(body, sign_key):
        return "reply signature verification failed"
    if is_absorption_result(body):
        if body.get("status") not in ("absorbed", "rejected", "isolated"):
            return f"reply has invalid status {body.get('status')!r}"
        if not _usable(body.get("packet_ref")):
            return "reply has empty packet_ref"
        if body["status"] in ("rejected", "isolated") and not _usable(body.get("reason")):
            return "reply is missing a non-empty reason"
        gate = gate_problem(body)
        if gate:
            return f"reply violates the hard gate: {gate}"
        expected = ref_of(sent)
        if expected != UNKNOWN_REF and body["packet_ref"] not in (expected, UNKNOWN_REF):
            return f"reply packet_ref {body['packet_ref']!r} does not match {expected!r}"
        if schema:
            problem = schema_problem(body, "absorption-result")
            if problem:
                return f"reply is not a valid Absorption Result: {problem}"
        return None
    if body.get("ack") is True and kind_of(sent) in ("agent-profile", "absorption-result"):
        return None
    return "reply is neither an Absorption Result nor an ack for this message type"


# ---------------------------------------------------------------------------
# Receiver decision for a message that passed check_incoming
# ---------------------------------------------------------------------------


def respond(msg: Message) -> Message:
    """Reply for a message that already passed ``check_incoming``.

    This implementation never returns ``absorbed``: it runs no local
    regression, so it cannot produce honest ``effect_summary`` evidence.
    """
    kind = kind_of(msg)
    if kind == "experience-packet":
        if ref_of(msg) == UNKNOWN_REF:
            return rejected(msg, "reserved or missing packet id: 'unknown' cannot be reported")
        method = (msg.get("absorption") or {}).get("method")
        if method not in MUST_METHODS:
            return rejected(msg, f"unsupported method: {method}")
        return {"packet_ref": msg["id"], "status": "isolated", "reason": ISOLATED_REASON}
    if kind == "negotiation":
        return rejected(
            msg,
            f"{REASON_NEGOTIATION}: second_impl does not implement negotiation; "
            "nested packets are not processed",
        )
    if kind == "absorption-result":
        return {"ack": True, "type": "result_ack"}
    return {"ack": True, "type": "profile_ack"}


def load_json_file(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


__all__: List[str] = [
    "CLIENT_TIMEOUT_SECONDS",
    "ISOLATED_REASON",
    "MUST_METHODS",
    "ReplayWindow",
    "UNKNOWN_REF",
    "canonical_bytes",
    "check_incoming",
    "check_outgoing",
    "check_reply",
    "fresh_replay_fields",
    "gate_problem",
    "is_absorption_result",
    "kind_of",
    "parse_timestamp",
    "ref_of",
    "rejected",
    "respond",
    "schema_dir",
    "schema_problem",
    "sign",
    "verify",
    "version_problem",
]
