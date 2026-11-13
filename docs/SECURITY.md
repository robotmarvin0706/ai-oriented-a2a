# Security Recommendations (non-normative)

This document does not change wire requirements in SPEC.md. It records minimal practices that reduce the cost of accepting external Experience Packets while preserving peer autonomy. The HTTP binding and recommended error codes are in [TRANSPORT.md](TRANSPORT.md). Version and unknown-field rules are in [VERSIONING.md](VERSIONING.md).

## Threat model (minimal)

- Message forgery or tampering: an unauthenticated peer can inject a packet that alters local policy.
- Replay: a previously absorbed packet is re-sent to re-trigger isolation or consume budget.
- Pollution via absorption: a packet that passes schema but produces negative ΔSR after local regression.
- Unbounded sending: a peer floods packets, exhausting isolation trials or token budget.

No central registry or mandatory human mediation is assumed. Receivers remain free to reject or isolate.

## Recommended practices

### Message authenticity
Prefer an optional signature over the canonical JSON of the message body.
- Recommended: Ed25519 or HMAC-SHA256.
- Signature covers the entire payload except the signature field itself.
- Receivers that do not implement verification MAY ignore the field; they SHOULD log the absence when accepting packets from unknown peers.

### Identity binding
Agent Profile MAY include an optional `identity` object containing a public-key fingerprint or opaque identifier.
- Not required for v0.2 compliance.
- When present, receivers can bind subsequent packets from the same peer to the same identity for local accounting.

### Replay window
Messages MAY include optional `nonce` (opaque string) and `timestamp` (ISO-8601 or Unix seconds).
- Receivers SHOULD maintain a short sliding window (suggested 5–15 minutes) of seen nonces per peer.
- Packets outside the window or with duplicate nonces SHOULD be rejected with reason `replay_or_stale`.

### Absorption gate
Even with a valid signature, the receiver MUST still run its local regression and isolation path (see IMPLEMENTATION.md).
- A signed packet that fails the ΔSR threshold is rolled back exactly as an unsigned one.
- Signature success does not override the SPEC §3.1 `effect_summary` hard gate (`note` MUST when `status=absorbed`) or declared rollback rules.

### Constraint check
Before absorption, the receiver SHOULD verify that the packet’s type and absorption method are permitted by its current `constraints` list.
- Violation → Absorption Result with status `rejected` and reason indicating the violated constraint.

## Relation to existing fields

- `provenance.source_ref` MAY point to the signing identity or key fingerprint.
- `verification_claim.expected_effect` remains a local claim; signature does not replace it.
- Absence of signature, nonce, or identity is not a protocol violation. Implementations that treat absence as automatic rejection are free to do so locally, but must still emit an explicit Absorption Result.

## Implementation note

`examples/network_dual_end.py` supports optional HMAC-SHA256 via `--sign-key` and optional `nonce` + `timestamp` replay protection (10-minute sliding window of seen nonces). Duplicate or out-of-window messages are rejected with reason `replay_or_stale` before absorption. Absence of either field is not a protocol violation. `examples/network_multi_node_demo.py` shows light interest-tag discovery across several local HTTP endpoints using the same path.

## Local logging

Receivers SHOULD record, at minimum:
- whether a signature was present and verified,
- whether a nonce was accepted or rejected as replay,
- the final absorption status and any rollback action.

These logs stay local and are not required on the wire.
