# Datasets

Public Experience Packet corpora for the tool-call timeout / failure-recovery task, plus counter-examples for the untrusted-content rule (SPEC §2.1). Every packet here follows [docs/DATA_POLICY.md](../docs/DATA_POLICY.md).

## Current status: synthetic packets plus one simulated experiment run

The hand-written corpora (`*/packets/`, `*/absorption_examples/`) are **synthetic**. Nothing in them comes from a real run, and nothing in them claims a measured improvement.

Since 0.4.1, `timeout_recovery/runs/` holds the output of the four-arm experiment in [`experiments/timeout_recovery/`](../experiments/timeout_recovery/). It is kept separate from the synthetic packets; see [Experiment runs](#experiment-runs-timeout_recoveryruns) below.

- Generation method: authored by hand from the timeout / failure-recovery scenario in [SPEC.md §5](../SPEC.md#5-minimal-dual-agent-experiment-priority-non-normative) and the timeout and retry guidance in [docs/TRANSPORT.md](../docs/TRANSPORT.md).
- Every packet carries `provenance.source_ref` with the `synthetic:` prefix.
- `verification_claim.expected_effect` is qualitative. No ΔSR or other numeric uplift is stated.

The rules above apply to the synthetic corpora. Run data (`local_run:` / `peer_absorbed:` prefixes) is kept in its own subfolder.

## Layout

```
datasets/
  README.md
  LICENSE                      CC0 1.0 for everything under datasets/
  timeout_recovery/
    packets/                   Experience Packets (pkt-synth-timeout-001 .. 008)
    absorption_examples/       Absorption Results that reference packets above
    runs/<seed>/               Experiment output (since 0.4.1): trajectories.jsonl, controls.jsonl
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

## Experiment runs (`timeout_recovery/runs/`)

Output of `python -m experiments.timeout_recovery.run --seeds 42 7 2026 --n 30` (since 0.4.1). The summary table and the verdict are in [`experiments/timeout_recovery/RESULTS.md`](../experiments/timeout_recovery/RESULTS.md).

- What it is: a real execution of the experiment host's tool-call loop against a **simulated** `web_search` tool (timeouts, latency, throttling and late partial results drawn from a seeded stream). It is not data from a real service. Every packet says so with `content.simulated_tool: true`.
- Seeds 42, 7 and 2026; 30 cases per arm per seed. Rerunning the command gives byte-identical files (no wall-clock time is written; ids and draws come from sha256). `tests/experiments/` checks this and checks that the committed files match a fresh run.
- `trajectories.jsonl`: one v0.4 Experience Packet per line, one per arm (`no_sharing`, `own_history`, `raw_trajectory`, `distilled_packet`) and case. `content.episodes` lists the evaluation episodes (window, success, failure reason, tool calls, tokens, and a compact trace: `T` timeout, `O` ok, `R` throttled, `D` stale partial dropped, `A` stale partial accepted, with the backoff wait in ms before a step).
- `controls.jsonl`: the same for the four control packets (`wrong_packet`, `empty_packet`, `irrelevant_packet`, `injection_packet`).
- Per-seed aggregates (per-arm metrics, the fixed decision checks, the control checks) are in `experiments/timeout_recovery/summaries/seed-<seed>.json`, not here: every `*.json` file under `datasets/` is checked as a protocol message.
- Provenance: `provenance.source_ref` is `local_run:tr-sim-v1-s<seed>` (an opaque run id, no timestamp or key material). These packets carry a measured `verification_claim.value` (the case's recovery success rate) with `scenario_ref`, `metric` (`recovery_sr`) and `repro` (`seed`, `model` = `deterministic-rule-host-v1`, `n`), so they are not marked `synthetic:`. The value describes the simulator, not a real tool.
- Format: JSON Lines, so the per-file checks in `tests/interop/test_datasets.py` (which read `*.json` and `packets/`) do not apply to them; `tests/experiments/test_timeout_recovery.py` validates every line against `schema/experience-packet.schema.json`, checks the provenance prefix and `verification_claim` fields, and checks that no content matches a forbidden directive pattern.
- Size: about 1.25 MB for all three seeds (limit 2 MB).

## License

Everything under `datasets/` is dedicated to the public domain under CC0 1.0. See [LICENSE](LICENSE). Code in this repository stays under the MIT license in the repository root.

## Validate locally

```bash
pip install -e ".[schema]" pytest referencing -q
pytest tests/interop/test_datasets.py tests/interop/test_content_policy.py -q
```
