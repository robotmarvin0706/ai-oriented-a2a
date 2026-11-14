# Compliance and conformance testing

This document lists what an implementation of AI-oriented A2A v0.3 must do to interoperate, and which tests in this repository check it. It adds no wire requirements: the normative sources are [SPEC.md](../SPEC.md), [TRANSPORT.md](TRANSPORT.md) (HTTP binding and error semantics), [VERSIONING.md](VERSIONING.md), and the schemas in `schema/`.

Two implementations are checked against these tests today:

- the reference: `ai_oriented_a2a` plus `examples/network_dual_end.py`;
- an independent second implementation: `second_impl/`, written from SPEC and TRANSPORT only, which does not import the reference code.

## 1. Minimum message support

| Message | Receiver MUST |
| --- | --- |
| Experience Packet | Reply with an Absorption Result (`absorbed`, `rejected`, or `isolated`) whose `packet_ref` is the packet `id`. Reject methods it cannot execute (`parameter_delta` unless supported with verify+rollback). Never silently discard. |
| Absorption Result (pushed by a peer) | Apply the hard gate: `status=absorbed` without a non-empty `effect_summary.note` is a protocol violation and is answered with a rejected Absorption Result. A valid result MAY be acknowledged with an ack object. |
| Agent Profile | Accept it. The reply MAY be an ack object; acks are not protocol message types. |
| Negotiation (propose / accept / reject / counter) | Either implement it, emitting an Absorption Result for every nested packet (SPEC §3.1), or reject it with a valid Absorption Result whose reason starts with `negotiation_unsupported:` and whose `packet_ref` is the `offered_packet.id` (else `unknown`). |

## 2. Error semantics (HTTP binding)

From [TRANSPORT.md](TRANSPORT.md#http-status-codes):

- Application-level failures return **HTTP 200** with a **valid Absorption Result**: `status=rejected`, a non-empty `packet_ref`, and a non-empty `reason`. Never HTTP 401/403/409/422, never an empty `packet_ref`, never HTML.
- `packet_ref` is the request `id` (or its `packet_ref`), else the reserved literal `unknown`. `unknown` is never used with `status=absorbed`.
- The `version` field is checked before full schema validation. An unknown version (`"v0.3"`, `"0.4"`, a non-string, ...) gives `reason` containing `unsupported_version`, never a schema error and never `isolated`.
- Signature and replay checks are off unless enabled. When enabled, failures give `signature verification failed` or a reason starting with `replay_or_stale:`. With a replay window on, both `nonce` and `timestamp` are required.
- An unparseable body gives HTTP 400 with a valid Absorption Result (`packet_ref` = `unknown`, `reason` = `invalid json`).
- Reason strings use the reserved prefixes in [TRANSPORT.md](TRANSPORT.md#reserved-reason-prefixes): `unsupported_version:`, `replay_or_stale:`, `schema_invalid:`, `negotiation_unsupported:`, and the informative `isolated:`.
- Signature: HMAC-SHA256 over the UTF-8 bytes of `json.dumps(body_without_signature, separators=(",", ":"), sort_keys=True, ensure_ascii=False)`, carried as `signature: {"alg": "hmac-sha256", "value": "<hex>"}`.

## 3. Test sets

All paths are under `tests/`. "Wire" tests talk HTTP to a running receiver and apply to any implementation; "artifact" tests check the published schemas, examples, and datasets that every implementation must accept.

| Test file | Kind | What a second implementation must satisfy |
| --- | --- | --- |
| `conformance/test_http_conformance.py` | wire, **required** | The 12 HTTP cases: unknown version (incl. `v0.3`) → 200 + rejected; reserved `unknown` ref; signature failure; replay only when enabled; ISO-8601 timestamps; missing nonce/timestamp with the window on; sign and replay independent; omitted version; invalid JSON → JSON body; incoming hard-gate violation; never absorb under `unknown`. |
| `interop/test_schema_and_rules.py` | artifact | Examples validate; unsupported methods are rejectable; packet `id` required; `packet_ref` matches `id`. |
| `interop/test_absorption_gate.py` | artifact + reference | The 11 schema/example cases (absorbed needs a non-empty `note`; rejected/isolated need `reason`; metrics may be booleans) apply to everyone. The last 3 test the reference experiment path (`minimal_dual_end`) and are not required of other implementations. |
| `interop/test_boundary.py` | artifact | Nested packets carry their own `id`; `effect_summary` shape; rejected needs `reason`. |
| `interop/test_optional_fields.py` | artifact + reference | Schema cases for `version` / `nonce` / `timestamp` / `signature` / `identity` and the published signed example apply to everyone. The HTTP cases are the same as `conformance/` but bound to the reference handler. Helper-function tests (`canonical`, `check_replay`, ...) are reference-specific; the behaviour they pin is covered for `second_impl` by `interop/test_second_impl.py`. |
| `interop/test_datasets.py` | artifact | Every message under `datasets/` must be accepted by a compliant receiver (`source_ref` prefixes, verification claims, chain length, CC0). |
| `interop/test_package.py` | reference only | Exports and behaviour of the `ai_oriented_a2a` package. |
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

**Recommended: a launch command.** The runner starts a fresh process for every configuration a test needs, on a free port, so replay state never leaks between tests. The command template must contain `{port}`; the runner appends `--sign-key KEY` and/or `--replay-window SECONDS` when needed.

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

**A running peer.** Point the suite at a URL and describe its configuration with `A2A_PEER_SIGN_KEY` and `A2A_PEER_REPLAY_WINDOW` (leave them unset for a peer with both checks off). Tests that need another configuration are skipped, and because the peer keeps its replay state, restart it before each run.

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
