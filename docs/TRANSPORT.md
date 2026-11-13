# A2A Transport Contract

This document describes the HTTP binding used by `examples/network_dual_end.py`, aligned with [SECURITY.md](SECURITY.md) and the Absorption Result hard gate in [SPEC.md](../SPEC.md) §3.1. It introduces no new mandatory dependencies. The HTTP binding is **not** mandatory: other transports (gRPC, message queues, and similar) are allowed as long as they preserve the same JSON message shapes.

Where the reference example and this contract differ, the difference is called out. Recommended HTTP status codes below are SHOULD-level mappings; they are not yet all implemented by the example.

## Endpoint and media type
- Canonical HTTP endpoint: `POST /a2a/message`
- Request and response `Content-Type`: `application/json`
- Other paths MUST NOT be required. The reference example returns HTTP 404 for any other path.

## Request shape

The HTTP body **is the message itself** (an Agent Profile, an Experience Packet, a negotiation message, or an Absorption Result). There is no wrapper object such as `{ "version", "packet", ... }` in the current binding, and there is no on-wire `version` field. Protocol version is out-of-band; see [VERSIONING.md](VERSIONING.md).

Optional security fields MAY be attached to that message (all OPTIONAL; see [SECURITY.md](SECURITY.md)):

- `nonce`: opaque string
- `timestamp`: Unix seconds (number) or ISO-8601 string. SECURITY.md allows both; the reference example uses Unix seconds as a JSON number.
- `signature`: an object, not a bare string. The reference example uses HMAC-SHA256:

```json
{
  "alg": "hmac-sha256",
  "value": "hex-digest-over-canonical-json-excluding-this-field"
}
```

Example Experience Packet as posted (illustrative; required packet fields omitted with `...` only in this prose, not on the wire). A complete packet looks like:

```json
{
  "id": "pkt-experience-7f3a",
  "type": "failure_mode",
  "content": {
    "trigger": "tool_call_timeout",
    "compressed_signal": "apply exponential backoff"
  },
  "absorption": {
    "method": "prompt_injection",
    "hint": "prepend recovery instruction"
  },
  "nonce": "nonce-ab12cd34",
  "timestamp": 1731480000,
  "signature": {
    "alg": "hmac-sha256",
    "value": "0123456789abcdef"
  }
}
```

`nonce`, `timestamp`, and `signature` MAY all be omitted. When they are omitted and the receiver has not enabled the corresponding checks, the receiver MUST NOT reject an otherwise-valid message solely because these fields are absent.

## Response shape

On a successfully parsed request that is an Experience Packet, the receiver returns an Absorption Result (SPEC §3.1). Required and conditional fields are those of the Absorption Result, including `packet_ref`:

Absorbed (hard gate: `effect_summary.note` is MUST):

```json
{
  "packet_ref": "pkt-experience-7f3a",
  "status": "absorbed",
  "effect_summary": {
    "note": "local regression passed, SR 0.72→0.81"
  }
}
```

Rejected (`reason` is MUST; `effect_summary` is OPTIONAL):

```json
{
  "packet_ref": "pkt-experience-7f3a",
  "status": "rejected",
  "reason": "unsupported method: parameter_delta"
}
```

Isolated (`reason` is MUST, same as v0.2; `effect_summary` is OPTIONAL):

```json
{
  "packet_ref": "pkt-experience-7f3a",
  "status": "isolated",
  "reason": "content retained for inspection but not injected into active context"
}
```

`status` is one of `absorbed`, `rejected`, `isolated`. When `status` is `absorbed`, `effect_summary.note` is MUST (non-empty). Optional metric hints, when present, SHOULD be placed in `effect_summary.metrics`.

The reference example also returns small JSON acknowledgements for a Profile (`{"ack": true, "type": "profile_ack"}`) and for a received Absorption Result (`{"ack": true, "type": "result_ack"}`). Those acks are implementation conveniences, not additional protocol message types.

## HTTP status codes

### Current reference example (`examples/network_dual_end.py`)

- `200`: application outcomes, including a valid Absorption Result, signature verification failure, and replay rejection. The outcome is in the JSON body (`status` / `reason`), not in the HTTP status.
- `400`: invalid JSON. The example currently uses `BaseHTTPRequestHandler.send_error`, whose body is HTML rather than JSON — a known gap versus the MUST in “Other” below.
- `404`: path is not `/a2a/message`.

The example does not emit `401`, `403`, `409`, or `422`. Signature and replay failures are HTTP 200 with `status` = `rejected` and a `reason` (replay reasons contain `replay_or_stale`). A hard-gate failure at the emitter is converted to `status` = `rejected` (still HTTP 200) by `Receiver.absorb`, so an invalid `absorbed` result is not sent.

### Recommended mappings (SHOULD)

Implementations MAY adopt these HTTP codes without changing JSON message shapes:

- `400`: invalid JSON or schema validation failure of the request. The body MUST be a parseable JSON error object.
- `401` / `403`: signature verification failure, when signature checking is enabled.
- `409`: `replay_or_stale` (nonce or timestamp check failure, when replay protection is enabled).
- `422`: a received Absorption Result violates the hard gate (for example `status=absorbed` with a missing or empty `note`). An emitter that detects this locally SHOULD instead emit `status=rejected` with a `reason`, as the reference receiver does, and MAY still use HTTP 200 for that valid rejected result.
- Other transport-layer errors follow HTTP semantics (`5xx` for server failures, and so on).

## Security and replay protection (all OPTIONAL)

- Signature, `nonce`, and `timestamp` are all optional on the wire.
- When the corresponding check **is enabled**: the receiver SHOULD verify the signature and/or the time window; failure returns an explicit error (currently HTTP 200 + `rejected` in the example; recommended `401` / `403` / `409` above).
- When the corresponding check **is not enabled**: the receiver MUST NOT reject an otherwise-valid packet solely because these fields are absent.
- A receiver that has chosen a local policy of “require signature” (or require nonce) has enabled that check. [SECURITY.md](SECURITY.md) allows that local policy provided the receiver still emits an explicit Absorption Result (or a JSON error under the recommended codes).

## Timeouts and retries (SHOULD)

- Recommended client timeout is at least 30 seconds (absorption MAY include local regression). The reference example currently uses 10 seconds for `send_message` and 5 seconds for a pushed result — a known gap versus this recommendation.
- Retryable: network failures, `5xx`, timeouts.
- Non-retryable: other `4xx` (except `408` and `429`), hard-gate violations, and an explicit Absorption Result with `status=rejected`.
- On retry, if replay protection is enabled, the client SHOULD use a **fresh** `nonce` (and a current `timestamp`). Reusing a nonce is itself rejected as `replay_or_stale`.

## Other

- Failure responses MUST return a parseable JSON error object (or a well-formed Absorption Result with `status=rejected` or `isolated`) so peers can distinguish “peer rejection” from “transport failure”. The reference example’s HTTP 400 path does not yet meet this MUST; that is a follow-up for the example, not a new protocol dependency.
- This contract is based on the existing behavior of `examples/network_dual_end.py` and [SECURITY.md](SECURITY.md).
