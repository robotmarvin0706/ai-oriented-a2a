"""Minimal stdlib library for the AI-oriented A2A peer protocol (v0.4).

Required dependencies: none (Python standard library only). Schema
validation is an optional extra: ``pip install 'ai-oriented-a2a[schema]'``.

Signature verification and the replay window stay off unless the
caller passes ``sign_key`` or ``replay_window``. The HTTP binding (client,
request handler, threaded server) lives in ``ai_oriented_a2a.transport``;
a plain ``import ai_oriented_a2a`` does not load it. There is no central
component in this package.

SPEC §2.1 (untrusted content) helpers live in ``ai_oriented_a2a.content``
and are re-exported here: ``scan_directives``, ``wrap_untrusted`` and
``content_policy_rejection`` (standard library only).
"""

from .content import content_policy_rejection, scan_directives, wrap_untrusted
from .protocol import (
    CURRENT_MAJOR_MINOR,
    PROTOCOL_VERSION,
    RESERVED_PACKET_REF,
    SUPPORTED_MAJOR_MINOR,
    VERSION_PATTERN,
    ReplayCache,
    canonical,
    check_incoming,
    check_replay,
    new_id,
    packet_ref_of,
    parse_timestamp,
    reject_result,
    sign_message,
    supported_version,
    verify_message,
    version_rejection,
)
from .validate import SchemaDependencyError, SCHEMA_KINDS, schema_dir, validate

__version__ = "0.4.0"

__all__ = [
    "CURRENT_MAJOR_MINOR",
    "PROTOCOL_VERSION",
    "RESERVED_PACKET_REF",
    "SCHEMA_KINDS",
    "SUPPORTED_MAJOR_MINOR",
    "VERSION_PATTERN",
    "ReplayCache",
    "SchemaDependencyError",
    "__version__",
    "canonical",
    "check_incoming",
    "check_replay",
    "content_policy_rejection",
    "new_id",
    "packet_ref_of",
    "parse_timestamp",
    "reject_result",
    "scan_directives",
    "schema_dir",
    "sign_message",
    "supported_version",
    "validate",
    "verify_message",
    "version_rejection",
    "wrap_untrusted",
]
