# Datasets

Public Experience Packet corpora for the tool-call timeout / failure-recovery task, plus counter-examples for the untrusted-content rule (SPEC §2.1). Every packet here follows [docs/DATA_POLICY.md](../docs/DATA_POLICY.md).

## Current status: synthetic only

All data in this directory is **synthetic**. Nothing here comes from a real run, and nothing here claims a measured improvement.

- Generation method: authored by hand from the timeout / failure-recovery scenario in [SPEC.md §5](../SPEC.md#5-minimal-dual-agent-experiment-priority-non-normative) and the timeout and retry guidance in [docs/TRANSPORT.md](../docs/TRANSPORT.md).
- Every packet carries `provenance.source_ref` with the `synthetic:` prefix.
- `verification_claim.expected_effect` is qualitative. No ΔSR or other numeric uplift is stated.

Real-run data (`local_run:` / `peer_absorbed:` prefixes) will be kept separate from synthetic data when it is added, in its own subfolder.

## Layout

```
datasets/
  README.md
  LICENSE                      CC0 1.0 for everything under datasets/
  timeout_recovery/
    packets/                   Experience Packets (pkt-synth-timeout-001 .. 008)
    absorption_examples/       Absorption Results that reference packets above
  content_policy/
    packets/                   Counter-examples with injected directives (pkt-synth-inject-001 .. 002)
    absorption_examples/       The expected content_policy: rejection
```

Packet coverage in `timeout_recovery/packets/`:

| Id | Signal |
| --- | --- |
| `pkt-synth-timeout-001` | Single retry with exponential backoff, short-cache fallback |
| `pkt-synth-timeout-002` | Age-bounded cache fallback; never serve stale results silently |
| `pkt-synth-timeout-003` | Client timeout of at least 30s, aligned with TRANSPORT |
| `pkt-synth-timeout-004` | Counter-example (`content.counter_example: true`): 5s client timeout |
| `pkt-synth-timeout-005` | Fresh nonce and current timestamp on every retry when the replay window is on |
| `pkt-synth-timeout-006` | Do not retry an explicit rejection or a non-retryable 4xx |
| `pkt-synth-timeout-007` | Retry only 408 / 429 / 5xx / timeout under a fixed budget; chain of exactly 3 hops |
| `pkt-synth-timeout-008` | Truncated chain: last 3 hops kept, `content.chain_truncated: true` |

Packet coverage in `content_policy/packets/` (since v0.4). Both are counter-examples (`content.counter_example: true`) that hide a directive inside an otherwise ordinary recovery hint. A receiver is expected to reject them with a reason starting with `content_policy:` and never absorb them ([DATA_POLICY.md, Forbidden directive patterns](../docs/DATA_POLICY.md#forbidden-directive-patterns-normative)). CI checks that both in-repo implementations match and reject them.

| Id | Pattern(s) | Signal |
| --- | --- | --- |
| `pkt-synth-inject-001` | `ignore_prior_instructions` | Backoff hint followed by "ignore all previous instructions"; method `prompt_injection` |
| `pkt-synth-inject-002` | `disable_verification`, `bypass_isolation` | "Efficiency tip" to disable signature verification, skip the replay window, and absorb without isolation; method `retrieval` |

## License

Everything under `datasets/` is dedicated to the public domain under CC0 1.0. See [LICENSE](LICENSE). Code in this repository stays under the MIT license in the repository root.

## Validate locally

```bash
pip install -e ".[schema]" pytest referencing -q
pytest tests/interop/test_datasets.py tests/interop/test_content_policy.py -q
```
