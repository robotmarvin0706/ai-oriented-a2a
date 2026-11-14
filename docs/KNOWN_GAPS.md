# Known gaps

Tracked deviations and debts that are known but not fixed yet. Each entry says what is wrong, where, and the plan. Remove an entry in the PR that fixes it.

## Reference implementation (`examples/network_dual_end.py`)

### 1. Negotiation messages get a bare ack (violates SPEC §3.1)

A negotiation message (`proposal_id` present) is answered with `{"ack": true}`. A nested `offered_packet` or `conditional_packet` never gets an Absorption Result, but SPEC §3.1 says the receiver MUST emit one for every received Experience Packet, standalone or nested.

Plan: fix in the next PR. Either process the nested packet and return its Absorption Result, or reject the message with a valid Absorption Result whose reason starts with `negotiation_unsupported:` (`packet_ref` = `offered_packet.id`, else `unknown`), as `second_impl/` does.

### 2. Absorption outcome depends on `hash(id)`

The demo seeds its simulated regression with `hash(msg["id"])`. Python randomizes string hashes per process, so the same packet can be absorbed in one run and rejected in another. Tests therefore accept any of `absorbed` / `rejected` / `isolated` for a valid packet. Fixing it (for example with a stable digest) changes example logic and is out of scope here.

## Tests

### 3. HTTP cases exist in two places

`tests/conformance/test_http_conformance.py` contains copies of the 12 HTTP test functions in `tests/interop/test_optional_fields.py`, so they can run against any implementation. Until they are merged, a change to one copy MUST be made to the other.

Plan: in a follow-up PR, delete the HTTP section of `test_optional_fields.py` and keep only the parametrized conformance copy (the `reference` implementation is already one of its parameters). See `tests/conformance/README.md`.

### 4. One HTTP test needs Python 3.9+

`test_http_signature_failure_returns_200_rejected` (in both copies) uses the dict union operator `|`, which needs Python 3.9. On Python 3.8 it fails with `TypeError` whenever the reference happens to return `absorbed` (see item 2). CI runs Python 3.12, and the package and `second_impl/` themselves still support 3.8. Fix when the copies are merged.
