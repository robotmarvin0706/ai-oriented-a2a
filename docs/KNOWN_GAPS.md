# Known gaps

Tracked deviations and debts that are known but not fixed yet. Each entry says what is wrong, where, and the plan. Remove an entry in the PR that fixes it.

## Tests

### 1. HTTP cases exist in two places

`tests/conformance/test_http_conformance.py` contains copies of the 12 HTTP test functions in `tests/interop/test_optional_fields.py`, so they can run against any implementation. Until they are merged, a change to one copy MUST be made to the other.

Plan: in a follow-up PR, delete the HTTP section of `test_optional_fields.py` and keep only the parametrized conformance copy (the `reference` implementation is already one of its parameters). See `tests/conformance/README.md`.

### 2. One HTTP test needs Python 3.9+

`test_http_signature_failure_returns_200_rejected` (in both copies) uses the dict union operator `|`, which needs Python 3.9. On Python 3.8 it fails with `TypeError`, because the reference receiver returns `absorbed` for that packet (see item 1). CI runs Python 3.12, and the package and `second_impl/` themselves still support 3.8. Fix when the copies are merged.
