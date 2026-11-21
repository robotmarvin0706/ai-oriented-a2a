"""Minimal stdlib library for the AI-oriented A2A peer protocol (v0.3).

Required dependencies: none (Python standard library only). Schema
validation is an optional extra: ``pip install 'ai-oriented-a2a[schema]'``.

Signature verification and the replay window stay off unless the
caller passes ``sign_key`` or ``replay_window``. The HTTP binding (client,
request handler, threaded server) lives in ``ai_oriented_a2a.transport``;
a plain ``import ai_oriented_a2a`` does not load it. There is no central
component in this package.
"""

from .protocol import (
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

__version__ = "0.3.3"

__all__ = [
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
    "new_id",
    "packet_ref_of",
    "parse_timestamp",
    "reject_result",
    "schema_dir",
    "sign_message",
    "supported_version",
    "validate",
    "verify_message",
    "version_rejection",
]
