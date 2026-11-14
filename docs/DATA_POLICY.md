# Data Policy for Public Experience Packet Corpora (non-normative)

## Purpose

This document defines the minimum trust and safety conditions for **public** corpora of Experience Packets, such as the files under [`datasets/`](../datasets/). It is written from the side of the receiving AI: a packet in a public corpus should be safe to absorb and should carry a signal that the receiver can check for itself.

This document is non-normative. It does not change [SPEC.md](../SPEC.md) or the schemas under [`schema/`](../schema/). On the wire, `provenance` and `verification_claim` stay OPTIONAL. The rules below apply only to packets that are published as a public corpus.

## Redaction (MUST)

Before a packet is published, remove:

- API keys, tokens, passwords, and any other credentials.
- Private file paths and directory layouts.
- User identifiers: names, account ids, email addresses, handles.
- Full raw tool response payloads. Keep only the failure mode and the recovery decision points.
- Any detail that could be used to reconstruct the local environment, such as hostnames, IP addresses, internal URLs, or exact versions of private infrastructure.

When in doubt, compress further. A public packet should carry a recovery pattern, not a log.

## `provenance.source_ref` namespaces

Public corpus packets MUST carry `provenance.source_ref` with exactly one of these three prefixes, followed by a non-empty opaque part:

| Prefix | Meaning |
| --- | --- |
| `synthetic:<opaque>` | Hand-written or generated data. Not from a real run. |
| `local_run:<opaque_run_id>` | Distilled from a real local run. The run id MUST be opaque: it MUST NOT contain real key fingerprints or precise timestamps. |
| `peer_absorbed:<opaque>` | Received from a peer and absorbed locally before republishing. |

A receiver SHOULD treat any other prefix (for example `peer:` or `local:` as used in the protocol examples under `examples/`) as an **unknown source**, and use such a packet for retrieval indexing only.

## Provenance chain

- `provenance.chain` keeps at most the **3 most recent hops**.
- If a chain is longer, truncate it to the last 3 hops and mark the truncation inside the packet with `content.chain_truncated: true`. Optionally record how many hops were dropped with `content.chain_dropped_hops: <int>`.
- No new schema field is introduced for this. The marker lives in `content`.
- A chain is context, not proof of quality.

## `verification_claim`

Public corpus packets MUST include `verification_claim` with both:

- `expected_effect`: a qualitative condition that a receiver can test locally (for example "recover from a single tool timeout without unbounded retries").
- `replay_hint`: how to reproduce the scenario (for example "fixed budget of 3 tool calls, one forced timeout"). Scenario parameters such as call budgets or timeouts are fine here.

Synthetic packets MUST NOT state a concrete numeric uplift, for example "+0.2", "SR 0.72→0.81", or a percentage improvement.

## Synthetic data

- Synthetic data MUST be explicitly marked with the `synthetic:` prefix.
- Synthetic data MUST NOT claim a real ΔSR or any other measured improvement.
- An Absorption Result shipped next to synthetic packets follows the same rule: its `effect_summary.note` stays qualitative.

## Before absorption (recommendation)

A receiver SHOULD use a public packet without a legal provenance prefix for `retrieval` only, and SHOULD NOT absorb it via `prompt_injection` or `trajectory_replay`. Packets with a legal prefix still go through the receiver's normal local checks; a legal prefix is not a guarantee of quality.

Counter-example packets (marked `content.counter_example: true`) describe what not to do. Index them for retrieval; do not inject them as rules.

## License

The corpus under `datasets/` is dedicated to the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/legalcode) (see [`datasets/LICENSE`](../datasets/LICENSE)). Code in this repository stays under the MIT [LICENSE](../LICENSE).

## How CI enforces this

[`tests/interop/test_datasets.py`](../tests/interop/test_datasets.py) runs in the `docs-and-examples` workflow and checks every JSON file under `datasets/`:

- Every file parses.
- Files under `packets/` validate against `schema/experience-packet.schema.json` and carry a `source_ref` with a legal prefix and a non-empty opaque part, a non-empty `expected_effect` and `replay_hint`, and a chain of at most 3 hops.
- Synthetic packets contain no numeric-uplift pattern in `content`, `expected_effect`, or `replay_hint`.
- Files under `absorption_examples/` validate against `schema/absorption-result.schema.json`, reference a packet id from the same dataset, and keep `note` free of numeric uplift.

The schema itself still accepts packets without `provenance`; the test file checks that too.
