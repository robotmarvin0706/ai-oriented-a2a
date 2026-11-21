# Compliance and conformance testing

This document lists what an implementation of AI-oriented A2A v0.4 must do to interoperate, and which tests in this repository check it. It adds no wire requirements: the normative sources are [SPEC.md](../SPEC.md), [TRANSPORT.md](TRANSPORT.md) (HTTP binding and error semantics), [VERSIONING.md](VERSIONING.md), and the schemas in `schema/`.

Two implementations are checked against these tests today:

- the reference: `ai_oriented_a2a` plus `examples/network_dual_end.py`;
- an independent second implementation: `second_impl/`, written from SPEC, TRANSPORT, and DATA_POLICY only, which does not import the reference code.

## 1. Minimum message support

| Message | Receiver MUST |
| --- | --- |
| Experience Packet | Reply with an Absorption Result (`absorbed`, `rejected`, or `isolated`) whose `packet_ref` is the packet `id`. Reject methods it cannot execute (`parameter_delta` unless supported with verify+rollback). Never silently discard. Since v0.4 (SPEC §2.1): treat `content` as data, never as instructions; wrap it in documented untrusted markers for `prompt_injection`; never auto-absorb content with a forbidden directive pattern, and SHOULD reject it with `content_policy:`. |
| Absorption Result (pushed by a peer) | Apply the hard gate: `status=absorbed` without a non-empty `effect_summary.note` is a protocol violation and is answered with a rejected Absorption Result. A valid result MAY be acknowledged with an ack object. |
| Agent Profile | Accept it. The reply MAY be an ack object; acks are not protocol message types. |
| Negotiation (propose / accept / reject / counter) | Either implement it, emitting an Absorption Result for every nested packet (SPEC §3.1), or reject it with a valid Absorption Result whose reason starts with `negotiation_unsupported:` and whose `packet_ref` is the `offered_packet.id` (else `unknown`). Nested `offered_packet` / `conditional_packet` content is untrusted data too (SPEC §2.1); both in-repo receivers reject a nested match with `content_policy:` and the nested `id` before any negotiation decision. Since v0.4 (SPEC §3.0): a propose or counter whose `target_constraint` is one of the receiver's `core_constraints` MUST be rejected with `immutable_constraint:`; a receiver that caps rounds (SHOULD, default 3 per `negotiation_id`) MUST reject a message over the cap with `negotiation_limit:`, and MUST NOT reject a message only because it lacks `negotiation_id`. Order: `content_policy:`, `negotiation_limit:`, `immutable_constraint:`, then anything else. |

## 2. Error semantics (HTTP binding)

From [TRANSPORT.md](TRANSPORT.md#http-status-codes):

- Application-level failures return **HTTP 200** with a **valid Absorption Result**: `status=rejected`, a non-empty `packet_ref`, and a non-empty `reason`. Never HTTP 401/403/409/422, never an empty `packet_ref`, never HTML.
- `packet_ref` is the request `id` (or its `packet_ref`), else the reserved literal `unknown`. `unknown` is never used with `status=absorbed`.
- The `version` field is checked before full schema validation. `"0.4"` and `"0.3"` are both supported by a v0.4 receiver. An unknown version (`"v0.4"`, `"0.5"`, a non-string, ...) gives `reason` containing `unsupported_version`, never a schema error and never `isolated`.
- Signature and replay checks are off unless enabled. When enabled, failures give `signature verification failed` or a reason starting with `replay_or_stale:`. With a replay window on, both `nonce` and `timestamp` are required.
- An unparseable body gives HTTP 400 with a valid Absorption Result (`packet_ref` = `unknown`, `reason` = `invalid json`).
- A receiver whose own handler fails returns HTTP 500 with a valid rejected Absorption Result whose reason starts with `internal_error:` and contains no exception text, traceback, or path.
- A missing, invalid, or too-large `Content-Length` (over 1048576 bytes for the in-repo implementations) returns HTTP 413 with a valid rejected Absorption Result whose reason starts with `payload_too_large:`, decided before the body is read.
- With a replay window on, a non-finite numeric `timestamp` (`NaN`, `Infinity`, `-Infinity`) is rejected with `replay_or_stale: invalid timestamp`.
- Concurrent requests to a threaded receiver all get a legal reply; the replay window is shared across request threads.
- Reason strings use the reserved prefixes in [TRANSPORT.md](TRANSPORT.md#reserved-reason-prefixes): `unsupported_version:`, `replay_or_stale:`, `schema_invalid:`, `negotiation_unsupported:`, `payload_too_large:`, `internal_error:`, `content_policy:`, `negotiation_limit:`, `immutable_constraint:` (the last three since v0.4), and the informative `isolated:`.
- String `timestamp` values are ISO-8601 with a timezone, matching the schema pattern `YYYY-MM-DDTHH:MM:SS[.fff|.ffffff](Z|±HH:MM)`. Numeric strings and strings without a timezone are schema-invalid. With a replay window on, a nonce that was already accepted is rejected whatever its `timestamp`.
- Signature: HMAC-SHA256 over the UTF-8 bytes of `json.dumps(body_without_signature, separators=(",", ":"), sort_keys=True, ensure_ascii=False)`, carried as `signature: {"alg": "hmac-sha256", "value": "<hex>"}`.

## 3. Test sets

All paths are under `tests/`. "Wire" tests talk HTTP to a running receiver and apply to any implementation; "artifact" tests check the published schemas, examples, and datasets that every implementation must accept.

| Test file | Kind | What a second implementation must satisfy |
| --- | --- | --- |
| `conformance/test_http_conformance.py` | wire, **required** | The 12 HTTP cases: unknown version (incl. `v0.3`) → 200 + rejected; reserved `unknown` ref; signature failure; replay only when enabled; ISO-8601 timestamps; missing nonce/timestamp with the window on; sign and replay independent; omitted version; invalid JSON → JSON body; incoming hard-gate violation; never absorb under `unknown`. |
| `conformance/test_robustness.py` | wire, **required** | Since 0.3.3: non-finite `timestamp` (`NaN`, `Infinity`, `-Infinity`) with the window on → 200 + `replay_or_stale: invalid timestamp`; missing / invalid / too-large `Content-Length` → 413 + `payload_too_large:` without reading the body; a body of exactly 1048576 bytes is accepted; negotiation → rejected `negotiation_unsupported:` (never a bare ack). The 16-thread concurrency case needs an in-process threaded server, so it runs only for `reference` (`ai_oriented_a2a.transport.make_server`) and `second_impl` (`second_impl.minimal_client.make_server`), not for `external`. |
| `conformance/test_untrusted_content.py` | wire, **required** for v0.4 | Since 0.4.0 (SPEC §2.1): the two `datasets/content_policy/` counter-examples, posted standalone and nested as `offered_packet`, → 200 + rejected, reason starting with `content_policy:`, `packet_ref` = the packet `id`, never `absorbed`; on-wire `"0.3"` and `"0.4"` are both accepted; a `"0.3"` message is still checked for content. |
| `conformance/test_core_constraints.py` | wire, **required** for v0.4 | Since 0.4.0 (SPEC §3.0), with the receiver's core constraints set to `["no parameter_delta"]` where needed: a propose or counter targeting it → 200 + rejected `immutable_constraint:` (exact match only; `0.3` and `0.4` alike); the 4th message with one `negotiation_id` → `negotiation_limit:`, the first 3 are not; messages without `negotiation_id` (different `proposal_id`s) are never limited; the cap is per id; `content_policy:` beats `immutable_constraint:`, and `negotiation_limit:` beats `immutable_constraint:`; a profile with `core_constraints` is accepted. Every id is fresh per test. |
| `interop/test_negotiation_limits.py` | artifact + reference + second_impl | `core_constraints`, `negotiation_id` (all four branches) and counter `target_constraint` are OPTIONAL in the schemas; old messages still validate. Default cap 3 in both implementations, overridable; no counting without `negotiation_id`; bounded table; per-peer rate limit off by default; both implementations give the same reason on the same message sequences. |
| `interop/test_schema_and_rules.py` | artifact | Examples validate; unsupported methods are rejectable; packet `id` required; `packet_ref` matches `id`. |
| `interop/test_absorption_gate.py` | artifact + reference | The 11 schema/example cases (absorbed needs a non-empty `note`; rejected/isolated need `reason`; metrics may be booleans) apply to everyone. The last 3 test the reference experiment path (`minimal_dual_end`) and are not required of other implementations. |
| `interop/test_boundary.py` | artifact | Nested packets carry their own `id`; `effect_summary` shape; rejected needs `reason`. |
| `interop/test_optional_fields.py` | artifact + reference | Schema cases for `version` / `nonce` / `timestamp` / `signature` / `identity` and the published signed example apply to everyone. The HTTP cases are the same as `conformance/` but bound to the reference handler. Helper-function tests (`canonical`, `check_replay`, ...) are reference-specific; the behaviour they pin is covered for `second_impl` by `interop/test_second_impl.py`. |
| `interop/test_datasets.py` | artifact | Every message under `datasets/` must be schema-valid and follow DATA_POLICY (`source_ref` prefixes, verification claims, no `verification_claim.value` on synthetic packets, chain length, CC0). The `content_policy/` counter-examples are schema-valid but are expected to be rejected (see `test_content_policy.py`). |
| `interop/test_content_policy.py` | artifact + reference + second_impl | Forbidden directive patterns: names and order equal the DATA_POLICY table in both implementations; a probe list (including every DATA_POLICY example) gives the same hits in both; no hits on the benign corpus and `examples/`; counter-examples rejected by both; `wrap_untrusted` markers, JSON-data body, no early close, byte-equal with `second_impl`; `prompt_injection` in the reference demo injects the wrapped packet, not the hint; `verification_claim` OPTIONAL fields in the schema. |
| `interop/test_package.py` | reference only | Exports and behaviour of the `ai_oriented_a2a` package. |
| `interop/test_timestamp_replay.py` | artifact + reference | The ISO-8601 timestamp pattern in all 7 schema spots (valid strings and numbers pass; numeric strings, no timezone, date-only, space separator, lowercase, `+0800`, 1- or 9-digit fractions fail) applies to everyone, as does "every schema-valid string parses in both implementations". The replay-cache tests (nonce-only duplicates, expired-then-oldest eviction, `SEEN_MAX` / `SEEN_TRIM_TO`) are reference-specific. |
| `interop/test_transport.py` | reference only | `ai_oriented_a2a.transport`: client and server round trip, signing, replay, `v0.3` rejection, invalid JSON → 400, unreachable host → `TransportError`, hard gate on replies, handler exception → 500 + `internal_error:` without details, keyword-only options, and that `import ai_oriented_a2a` does not load `http.server`. |
| `interop/test_second_impl.py` | second_impl | Unit tests for `second_impl/`: version first, gate before schema, `schema_invalid:`, signature byte-equality with the published example, replay keyed on nonce, never `absorbed`, negotiation rejection, CLI options, and independence (AST scan plus an isolated subprocess where importing the reference raises). |
| `interop/test_cross_impl.py` | wire | Live interop between `second_impl` and `network_dual_end.py` in both directions with signing and the replay window on, plus a pushed Absorption Result. |
| `mcp_bridge/test_bridge.py` | optional | The MCP bridge (`mcp_a2a/`); needs the `[mcp]` extra (Python ≥ 3.10). |

## 4. Running the suite

```bash
pip install -e ".[schema]" pytest
pytest tests/interop/ tests/conformance/ -q
```

By default the conformance suite runs every case against the reference and `second_impl`, both in-process.

### Against an external implementation

The conformance fixture can drive any HTTP receiver that implements `POST /a2a/message`. Two modes:

**Recommended: a launch command.** The runner starts a fresh process for every configuration a test needs, on a free port, so replay state never leaks between tests. The command template must contain `{port}`; the runner appends `--sign-key KEY`, `--replay-window SECONDS`, and one `--core-constraint TEXT` per core constraint (since 0.4.0) when needed.

```bash
A2A_CONFORMANCE_IMPLS=external \
A2A_PEER_CMD="python3 path/to/your_receiver.py --port {port}" \
pytest tests/conformance/ -q
```

Wrap your binary in a small script if its flags differ. For example, both of these pass today:

```bash
A2A_CONFORMANCE_IMPLS=external A2A_PEER_CMD="python3 second_impl/minimal_client.py serve --port {port}" pytest tests/conformance/ -q
A2A_CONFORMANCE_IMPLS=external A2A_PEER_CMD="python3 examples/network_dual_end.py --role receiver --port {port}" pytest tests/conformance/ -q
```

**A running peer.** Point the suite at a URL and describe its configuration with `A2A_PEER_SIGN_KEY` and `A2A_PEER_REPLAY_WINDOW` (leave them unset for a peer with both checks off), and since 0.4.0 its core constraints with `A2A_PEER_CORE_CONSTRAINTS` as a JSON array (the core-constraint cases expect `["no parameter_delta"]`). Tests that need another configuration are skipped, and because the peer keeps its replay state, restart it before each run.

```bash
# peer started with signing and replay off (7 of the 12 cases run)
A2A_CONFORMANCE_IMPLS=external A2A_PEER_URL=http://127.0.0.1:8002 pytest tests/conformance/ -q

# peer started with --sign-key secret only, or with --replay-window 600 only
A2A_CONFORMANCE_IMPLS=external A2A_PEER_URL=http://127.0.0.1:8002 A2A_PEER_SIGN_KEY=secret pytest tests/conformance/ -q
A2A_CONFORMANCE_IMPLS=external A2A_PEER_URL=http://127.0.0.1:8002 A2A_PEER_REPLAY_WINDOW=600 pytest tests/conformance/ -q
```

The cases sign with the key `secret`. No case needs signing and the replay window at the same time. The three runs above together cover 10 of the 12 cases; the two that start receivers with different configurations in one test only run with `A2A_PEER_CMD`, which runs all 12 in one go. `A2A_CONFORMANCE_IMPLS` takes a comma-separated list (`reference`, `second_impl`, `external`).

The artifact tests need no running peer: an implementation in another language can reuse `schema/`, `examples/`, and `datasets/` directly as fixtures.

## 5. Known gaps

See [KNOWN_GAPS.md](KNOWN_GAPS.md) for deviations of the reference implementation and for the temporary duplication between `tests/conformance/` and `tests/interop/test_optional_fields.py`.
