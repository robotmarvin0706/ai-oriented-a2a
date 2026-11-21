# Conformance suite

HTTP-level tests that any AI-oriented A2A v0.3 receiver should pass. The `start_receiver` fixture in `conftest.py` is parametrized over implementations: the reference (`examples/network_dual_end.py`), `second_impl/`, and optionally an external implementation (`A2A_PEER_CMD` or `A2A_PEER_URL`). How to run it is in [docs/COMPLIANCE.md](../../docs/COMPLIANCE.md#4-running-the-suite).

## Duplication: keep in sync

`test_http_conformance.py` is a **copy** of the 12 HTTP test functions (and the `_post` / `_assert_valid_rejection` helpers) in `tests/interop/test_optional_fields.py`. Test bodies are unchanged; only the fixture differs.

Until the two are merged, any change to one copy MUST be made to the other in the same PR, so they do not drift.

Merge plan (tracked in [docs/KNOWN_GAPS.md](../../docs/KNOWN_GAPS.md)): remove the HTTP section from `test_optional_fields.py` and keep this parametrized copy, which already covers the reference implementation.

## Robustness cases (not duplicated)

`test_robustness.py` (since 0.3.3) has no copy elsewhere: non-finite timestamps, the 413 request-body limit, negotiation rejection, and 16 concurrent requests with distinct nonces. The first three use `start_receiver`. The concurrency case needs a `ThreadingHTTPServer`, and the `reference` receiver of `start_receiver` is `examples/network_dual_end.py` on a synchronous `HTTPServer`, so it uses its own fixture: `ai_oriented_a2a.transport.make_server` for `reference` and `second_impl.minimal_client.make_server` for `second_impl` (no `external` mode).
