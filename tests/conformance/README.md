# Conformance suite

HTTP-level tests that any AI-oriented A2A v0.4 receiver should pass. The `start_receiver` fixture in `conftest.py` is parametrized over implementations: the reference (`examples/network_dual_end.py`), `second_impl/`, and optionally an external implementation (`A2A_PEER_CMD` or `A2A_PEER_URL`). How to run it is in [docs/COMPLIANCE.md](../../docs/COMPLIANCE.md#4-running-the-suite).

## Duplication: keep in sync

`test_http_conformance.py` is a **copy** of the 12 HTTP test functions (and the `_post` / `_assert_valid_rejection` helpers) in `tests/interop/test_optional_fields.py`. Test bodies are unchanged; only the fixture differs.

Until the two are merged, any change to one copy MUST be made to the other in the same PR, so they do not drift.

Merge plan (tracked in [docs/KNOWN_GAPS.md](../../docs/KNOWN_GAPS.md)): remove the HTTP section from `test_optional_fields.py` and keep this parametrized copy, which already covers the reference implementation.

## Robustness cases (not duplicated)

`test_robustness.py` (since 0.3.3) has no copy elsewhere: non-finite timestamps, the 413 request-body limit, negotiation rejection, and 16 concurrent requests with distinct nonces. The first three use `start_receiver`. The concurrency case needs a `ThreadingHTTPServer`, and the `reference` receiver of `start_receiver` is `examples/network_dual_end.py` on a synchronous `HTTPServer`, so it uses its own fixture: `ai_oriented_a2a.transport.make_server` for `reference` and `second_impl.minimal_client.make_server` for `second_impl`. When `A2A_PEER_CMD` is set, the same case also runs against `external`: the peer is started as a fresh process on a free port (`shlex.split(template.format(port=port))` plus `--replay-window 600`, `subprocess.Popen` with `DEVNULL`, wait for the port, terminate and wait in teardown), so the implementation under test must genuinely serve 16 threads × 8 distinct nonces. An `A2A_PEER_URL`-only external peer is still not covered (its replay window is not ours to choose).

## Untrusted content cases (not duplicated)

`test_untrusted_content.py` (since 0.4.0, SPEC §2.1) has no copy elsewhere. It uses `start_receiver`, so it runs against `reference`, `second_impl`, and `external`: the `datasets/content_policy/` counter-examples, standalone and nested as `offered_packet`, must be rejected with a reason starting with `content_policy:`, and on-wire `"0.3"` and `"0.4"` must both be accepted.

## Core constraint cases (not duplicated)

`test_core_constraints.py` (since 0.4.0, SPEC §3.0) has no copy elsewhere. It uses `start_receiver(core_constraints=[...])`, so it runs against `reference`, `second_impl`, and `external` (passed as `--core-constraint TEXT` to `A2A_PEER_CMD`, or described by `A2A_PEER_CORE_CONSTRAINTS` for `A2A_PEER_URL`): a propose or counter targeting a core constraint → `immutable_constraint:`; the 4th message with one `negotiation_id` → `negotiation_limit:` (default cap 3); no counting without `negotiation_id`; and the order `content_policy:` → `negotiation_limit:` → `immutable_constraint:`. Ids are fresh per test, so a long-running peer does not carry rounds between tests.
