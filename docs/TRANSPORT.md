# A2A Transport Contract

This document describes the HTTP binding used by the demo server `examples/network_dual_end.py`, aligned with [SECURITY.md](SECURITY.md) and the Absorption Result hard gate in [SPEC.md](../SPEC.md) §3.1. Protocol helpers (canonical JSON, optional HMAC, optional replay window, version rejection, and legal rejected Absorption Results) live in the installable `ai_oriented_a2a` package; the example imports them and remains the HTTP server demo. It introduces no new mandatory dependencies. The HTTP binding is **not** mandatory: other transports (gRPC, message queues, and similar) are allowed as long as they preserve the same JSON message shapes.

Optional security fields (`nonce`, `timestamp`, `signature`) and the OPTIONAL on-wire `version` field are declared in the published schemas (`additionalProperties` remains `false`), so strict schema validation of messages that carry them can pass. Messages that omit them remain valid.

## Endpoint and media type
- Canonical HTTP endpoint: `POST /a2a/message`
- Request and response `Content-Type`: `application/json`
- Other paths MUST NOT be required. The reference example returns HTTP 404 for any other path.

## Request shape

The HTTP body **is the message itself** (an Agent Profile, an Experience Packet, a negotiation message, or an Absorption Result). There is no wrapper object such as `{ "version", "packet", ... }`. The message MAY include an OPTIONAL top-level `version` field (`"0.3"`, no leading `v`); if it is omitted, the receiver treats the message as the current normative version. See [VERSIONING.md](VERSIONING.md).

Optional security fields MAY be attached to that message (all OPTIONAL; see [SECURITY.md](SECURITY.md)):

- `nonce`: opaque string
- `timestamp`: Unix seconds (JSON number) or an ISO-8601 string **with a timezone**. Since library 0.3.2 the published schemas restrict the string form to `YYYY-MM-DDTHH:MM:SS`, an optional `.fff` or `.ffffff` fraction, and `Z` or `±HH:MM` (for example `2026-11-14T08:00:00Z`, `2026-11-14T16:00:00.123+08:00`). Numeric strings (`"1731480000"`), strings without a timezone, date-only strings, a space instead of `T`, lowercase `t`/`z`, and `+0800` fail schema validation. The pattern does not check calendar validity (for example `2026-02-30`); such a value passes the schema but fails timestamp parsing, which is a replay rejection when the window is on. The reference example sends integer Unix seconds.
- Compatibility: the reference parser (`ai_oriented_a2a.parse_timestamp`) still treats a naive (no timezone) string as UTC when the message reaches the replay check without schema validation. This is compatibility behaviour only; strict receivers MAY reject such strings with a `schema_invalid:` reason.
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
  "version": "0.3",
  "nonce": "nonce-ab12cd34",
  "timestamp": 1731480000,
  "signature": {
    "alg": "hmac-sha256",
    "value": "0123456789abcdef"
  }
}
```

`version`, `nonce`, `timestamp`, and `signature` MAY all be omitted. When they are omitted and the receiver has not enabled the corresponding checks, the receiver MUST NOT reject an otherwise-valid message solely because these fields are absent. When a replay window **has** been enabled, both `nonce` and `timestamp` MUST be present and valid; missing either is a replay rejection (`reason` starts with `replay_or_stale:`).

Canonical form for `signature.value` (HMAC-SHA256 in the reference implementation): UTF-8 bytes of `json.dumps(body, separators=(",", ":"), sort_keys=True, ensure_ascii=False)` where `body` is the message object with the `signature` field removed. Integer Unix seconds are recommended for `timestamp` so float formatting does not affect the digest. A complete, verifiable example (HMAC key `demo`) is `examples/experience-packet-with-security.json`.

## Version check before schema validation

Receivers MUST inspect the `version` field **before** performing full schema validation of the message. An unknown version (including a leading `v`, for example `"v0.3"`, and any value the receiver does not implement) MUST return HTTP 200 and a valid Absorption Result with `status=rejected` and a `reason` containing `unsupported_version`. The receiver MUST NOT first reject the message solely because it fails the schema `version` pattern (which would typically surface as a schema error rather than an Absorption Result). If the field is omitted, the receiver treats the message as the current normative version and MAY then apply full schema validation.

The installable library implements this order in `ai_oriented_a2a.check_incoming` (and `ai_oriented_a2a.version_rejection`). Receivers that use the optional `validate()` helper MUST call `check_incoming` or `version_rejection` first: `validate()` runs full schema validation and will raise `jsonschema.ValidationError` on an unknown version such as `"v0.3"` because the value fails the schema `version` pattern. The demo HTTP server (`examples/network_dual_end.py`) calls `version_rejection` immediately after JSON parsing, before signature and replay checks, and does not run a full schema validator on the request. See also [VERSIONING.md](VERSIONING.md).

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

The following application-level failures MUST return **HTTP 200** and a **valid Absorption Result** (`packet_ref` non-empty, `status` = `rejected`, `reason` present). They MUST NOT use a missing `packet_ref`, an empty-string `packet_ref`, or an HTML error body. They MUST NOT use HTTP `401`, `403`, `409`, or `422` for these outcomes. This replaces the earlier SHOULD mapping of those codes.

- Signature verification failure (when a signing key has been provided): `reason` is `signature verification failed`.
- Replay rejection (when a replay window has been provided): `reason` starts with `replay_or_stale:` (for example `replay_or_stale: duplicate nonce`).
- Unknown version (including a leading `v`, for example `"v0.3"`, and any value the receiver does not implement): `reason` contains `unsupported_version` (for example `unsupported_version: received 0.4`).
- Hard-gate failure: an emitter that would have sent `status=absorbed` without a non-empty `effect_summary.note` MUST instead emit `status=rejected` with a `reason`. A receiver that is given such an invalid `absorbed` result MUST also respond with HTTP 200 and a valid rejected Absorption Result, and MUST NOT treat the claim as a successful absorption.

`packet_ref` equals the request's `id` (or, if the request is itself an Absorption Result, its `packet_ref`). If the request has no usable `id` or `packet_ref`, `packet_ref` is the reserved literal `unknown`. `unknown` is reserved for those early failures; it MUST NOT be used as the `packet_ref` of a successful absorption (`status=absorbed`).

Examples:

```json
{
  "packet_ref": "pkt-experience-7f3a",
  "status": "rejected",
  "reason": "signature verification failed"
}
```

```json
{
  "packet_ref": "pkt-experience-7f3a",
  "status": "rejected",
  "reason": "replay_or_stale: duplicate nonce"
}
```

```json
{
  "packet_ref": "pkt-experience-7f3a",
  "status": "rejected",
  "reason": "unsupported_version: received 0.4"
}
```

```json
{
  "packet_ref": "unknown",
  "status": "rejected",
  "reason": "unsupported_version: received 0.4"
}
```

### Reserved reason prefixes

The `reason` of an Absorption Result is a free-form string. The prefixes below are **reserved**: they are conventions for the beginning of that existing `reason` string, so that independent implementations report the same failure the same way. They are **not** new fields; no schema changes, and receivers MUST NOT require them for messages to be valid. An implementation MUST NOT use a reserved prefix for a different meaning, and SHOULD use the matching prefix when it reports the listed condition. Text after the prefix is free-form.

| Prefix or string | Status | Meaning |
| --- | --- | --- |
| `unsupported_version:` | normative (contained in `reason`, see above) | Unknown or unimplemented `version`, including a leading `v` such as `"v0.3"`. `status=rejected`. |
| `replay_or_stale:` | normative (see above) | Replay window enabled and the message has a missing or invalid `nonce` / `timestamp`, a duplicate nonce, or a timestamp outside the window. `status=rejected`. |
| `signature verification failed` | normative exact string (see above) | Signing key provided and the signature is missing or does not verify. `status=rejected`. |
| `schema_invalid:` | reserved prefix (SHOULD) | The message fails the published JSON Schema for its type, checked after the version check and the hard gate. `status=rejected`. |
| `negotiation_unsupported:` | reserved prefix (SHOULD) | The receiver does not implement negotiation. The reply is still a valid Absorption Result: `status=rejected`, `packet_ref` = the `id` of `offered_packet` if present, otherwise `unknown`. |
| `internal_error:` | reserved prefix (SHOULD) | The receiver's own message handler failed. Sent with HTTP 500 (see below). `status=rejected`. The text after the prefix MUST be a generic description; it MUST NOT contain exception messages, tracebacks, file paths, or other internal details. The reference uses `internal_error: message handler failed`. |
| `isolated:` | informative | Convention for the reason of an `isolated` result, e.g. `isolated: no local regression available`. Peers MUST NOT parse it for meaning beyond `status=isolated`. |

The reference implementation also uses `invalid json` (HTTP 400, see below), `unsupported method: <method>`, and `hard-gate violation: ...`. These are recommended spellings, not reserved prefixes. The independent `second_impl/` uses the same strings.

### Other HTTP codes (reference example)

- `200`: all application outcomes above, and successful Absorption Results, profile acks, and result acks.
- `400`: unparseable request body (invalid JSON). The body is still a valid Absorption Result (`packet_ref` = `unknown`, `status` = `rejected`, `reason` = `invalid json`), not HTML. HTTP 400 is kept for this unparseable-body case because it is not one of the application-level failures listed above. (This closes the previous HTML-body gap.)
- `500`: the receiver's message handler raised an exception. The body is still a valid Absorption Result (`packet_ref` from the request, `status` = `rejected`, `reason` starting with `internal_error:`, signed when a signing key is configured), with no exception text, traceback, or path. Clients treat it as retryable like any `5xx`.
- `404`: path is not `/a2a/message`. The body of this path error may still be the stock HTTP HTML page; it is a transport-level routing error, not a protocol message.
- Other transport-layer errors follow HTTP semantics (`5xx` for server failures, and so on).

## Security and replay protection (all OPTIONAL)

- Signature, `nonce`, and `timestamp` are all optional on the wire, and are declared in the published schemas.
- Passing a signing key enables signature verification; passing a replay-window parameter enables replay checks. The two MAY be enabled independently. The default (neither passed) leaves both off, so old peers still work.
- When the corresponding check **is enabled**: the receiver SHOULD verify the signature and/or the time window; failure MUST return HTTP 200 and a valid rejected Absorption Result as specified above. When the replay window is enabled, both `nonce` and `timestamp` MUST be present and valid; missing either is a replay rejection.
- When the corresponding check **is not enabled**: the receiver MUST NOT reject an otherwise-valid packet solely because these fields are absent, and MUST NOT apply the corresponding check even if the fields are present. `nonce` and `timestamp` remain optional on the wire while the window is off.
- A receiver that has chosen a local policy of “require signature” (or require nonce) has enabled that check. [SECURITY.md](SECURITY.md) allows that local policy provided the receiver still emits an explicit Absorption Result.

## Timeouts and retries (SHOULD)

- Recommended client timeout is at least 30 seconds (absorption MAY include local regression). A library SHOULD use a timeout of at least 30 seconds. The demo HTTP server (`examples/network_dual_end.py`) uses 30 seconds for both `send_message` and a pushed result.
- Retryable: network failures, `5xx`, timeouts.
- Non-retryable: other `4xx` (except `408` and `429`), hard-gate violations, and an explicit Absorption Result with `status=rejected`.
- On retry, if replay protection is enabled, the client SHOULD use a **fresh** `nonce` (and a current `timestamp`). Reusing a nonce is itself rejected as `replay_or_stale`, even with a different `timestamp`.

### Replay cache bounds (reference implementation)

`check_replay` keys a duplicate on the nonce alone: a nonce already in the seen set is rejected whatever its `timestamp`. The seen set is a plain `set` of `(nonce, str(int(timestamp)))` entries. When it grows past `SEEN_MAX` (1000) entries it is trimmed to `SEEN_TRIM_TO` (800): expired entries (outside the window) are dropped first, then the entries with the oldest timestamps. Both are module constants in `ai_oriented_a2a.protocol`. An entry that is dropped while still inside the window can in theory be replayed until its timestamp leaves the window; see [SECURITY.md](SECURITY.md#replay-cache-bounds).

## Other

- Failure responses MUST return a parseable JSON error object, preferably a well-formed Absorption Result with `status=rejected` or `isolated`, so peers can distinguish “peer rejection” from “transport failure”. Missing fields, empty-string `packet_ref`, and HTML error bodies MUST NOT be used for the application-level failures listed above, and MUST NOT be used for the invalid-JSON path either.
- This HTTP contract is based on the existing behavior of the demo server `examples/network_dual_end.py` and [SECURITY.md](SECURITY.md). Protocol helpers live in the `ai_oriented_a2a` package.
- An independent second implementation of this binding lives in `second_impl/` (it does not import `ai_oriented_a2a`). The tests a receiver must pass, and how to run them against another implementation, are in [COMPLIANCE.md](COMPLIANCE.md).

## First-edition minimal library boundary

The first-edition installable library is the `ai_oriented_a2a` package (`pip install .` from a checkout). It is also packaged for PyPI as `ai-oriented-a2a` (`pip install ai-oriented-a2a`, import name `ai_oriented_a2a`). Core helpers include `canonical`, `sign_message`, `verify_message`, `check_replay`, `version_rejection`, `reject_result`, `packet_ref_of`, and the combined entry point `check_incoming(msg, sign_key=None, replay_window=None, seen=None)`. Signature verification and the replay window stay off unless the corresponding argument is passed. There is no central component in the package.

Since 0.3.2 the HTTP binding ships as the submodule `ai_oriented_a2a.transport` (standard library only). It is not re-exported from the top-level package, so `import ai_oriented_a2a` does not load `http.server`; import it explicitly:

```python
from ai_oriented_a2a.transport import send_message, make_server, serve
```

- `send_message(url, message, *, timeout=30.0, sign_key=None, add_replay_fields=False)` posts to `url` + `/a2a/message` and returns the JSON reply (also for HTTP error statuses with a JSON body). Network errors, timeouts, and non-JSON replies raise `TransportError`. Options are keyword-only.
- `A2ARequestHandler` (override `handle_message`), `make_handler(on_message, *, sign_key=None, replay_window=None)`, `make_server(on_message, port=0, host="127.0.0.1", ...)`, and `serve(...)` implement the receiver side: 404 for other paths, 400 + Absorption Result for invalid JSON, `check_incoming` before the callback, the hard gate (`guard_result`) on the reply, signed replies when a key is set, and 500 + `internal_error:` if the callback raises. The host callback still decides absorption.

Integrators SHOULD use `ai_oriented_a2a.transport` rather than copy code from `examples/`. `examples/network_dual_end.py` remains the demo dual-endpoint server; its `A2AHandler` now subclasses `A2ARequestHandler`.

In scope for the first-edition library:

- Version check before any further processing (`version_rejection` / `check_incoming`)
- Absorption hard gate (`status=absorbed` requires a non-empty `effect_summary.note`)
- Legal error responses: a valid Absorption Result that always carries a non-empty `packet_ref` and a `reason` on the failure modes above, via `check_incoming` / `reject_result`
- Optional HMAC-SHA256 verification and optional replay window, each enabled only when the corresponding parameter is passed (`canonical`, `sign_message`, `verify_message`, `check_replay`)

The HTTP binding itself (`POST /a2a/message`, status codes, timeouts) is specified in this document, shipped as `ai_oriented_a2a.transport`, and demonstrated by `examples/network_dual_end.py`.

Out of scope for the first edition (documentation first; implementation later):

- Library-level redaction of packet content. Redaction rules for public corpora are documented in [DATA_POLICY.md](DATA_POLICY.md) (non-normative).
- Provenance-chain implementation beyond the existing OPTIONAL `provenance` field shape

Public-corpus conventions (`source_ref` prefixes, chain truncation, verification claims, licensing) are now documented in [DATA_POLICY.md](DATA_POLICY.md) (non-normative) and checked for `datasets/` in CI.
