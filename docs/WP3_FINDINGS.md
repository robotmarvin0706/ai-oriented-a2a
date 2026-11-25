# WP3 findings: the gate carries the value, the protocol layer does not

> Four seeds (42, 7, 2026, 31337) at N=30. Per-seed tables and the raw verdicts:
> [`experiments/timeout_recovery/WP3_RESULTS.md`](../experiments/timeout_recovery/WP3_RESULTS.md).
> The decision rules (R1–R4) were pre-registered in
> [`experiments/timeout_recovery/run.py`](../experiments/timeout_recovery/run.py) before
> any run and were not edited to fit the result. Reproduce with
> `python -m experiments.timeout_recovery.wp3 --seeds 42 7 2026 31337 --n 30 --results --summary-dir`.

## The claim

In the one scenario this project can currently measure, **the value is in the host's
absorption gate, not in the protocol layer.** An Experience Packet delivered over the
protocol and the same content dropped in as a local file produce identical results once
both pass through the gate; the only observable difference is attribution. This is a
negative result about the envelope, and it is the strongest result the project has
produced so far.

## What was measured

`gate` here means the receiving host's own machinery: the SPEC §2.1 content check, the
isolation trials, the ΔSR threshold, the single-step rollback, and the §3.1 hard gate that
requires evidence before `absorbed`. The `protocol layer` means the envelope around the
same content: packet id, Absorption Result, and the HTTP binding.

Five arms on two hosts, five packets, four seeds:

| arm | what it is |
| --- | --- |
| `own_history` | the WP1 local search; no packet, no peer |
| `plain_file` | the packet content as file text, **no gate** (the counterfactual) |
| `plain_file_gated` | the same content through the host gate, no packet id, no Absorption Result |
| `protocol` | the packet path: version check, content policy, gate, Absorption Result, attribution |
| `retrieval_bridge` | local corpus lookup via the MCP tool, then the same gate |

`plain_file_gated` is charged the same resident-context cost as `protocol`, so R2 compares
decision behaviour, not accounting.

## Findings

### 1. The gate has value (R1: pass on all four seeds)

On a host that already recovers (`working_host`, SR 0.911–0.944), the ungated wrong packet
costs **−0.539 / −0.528 / −0.472 / −0.539 ΔSR**. With the gate the same packet ends at
**+0.000** and rolls back in 30/30 cases. The gate is not free: on `distilled_packet` it
accepts 27/30 rather than 30/30, because three cases fail the isolation threshold — about
+0.05 ΔSR given up in exchange for that protection.

### 2. The protocol layer adds no measurable increment (R2: `none` on all four seeds)

The paired `protocol − plain_file_gated` ΔSR on `distilled_packet` is **0.000** on both
hosts, with the 95% CI inside the pre-registered equivalence margin of **±0.05**. R2 is a
TOST-style equivalence test, so the precise claim is "no increment larger than 0.05 ΔSR",
not "exactly zero"; a CI that merely contains 0 would have been reported as `undetermined`,
not as evidence.

The one measured difference is **attribution**: policy changes traceable to a packet id
with a matching Absorption Result are 1.0 for `protocol` and 0.0 for `plain_file_gated`.

### 3. The retrieval path is feasible only if the host drops the text (R3: `false`)

Seven of the eight feasibility checks pass. The failure is
`naive_host.a_amortized_extra_tokens_within_cap`: with the retrieved text kept resident and
absorption amortized over the N cases, the retrieval path costs 578.9 tokens/case against
`own_history`'s 124.1 (cap 1.5× → 186.1). Under view B — parsed parameters only, text
dropped — it is 182.9 against 186.1 and passes on every seed. The retrieval path is
therefore a **formatting** question: parse to parameters, do not keep the prose.

### 4. Safety holds (R4: pass)

The injection packet is rejected with `content_policy:` on every gated arm, 30/30 per seed,
and accepted in none. In the LLM-receiver run it never reached the model at all
(`model calls = 0`).

## What this does not show

- **One scenario, one simulated tool.** Tool-call timeout recovery on a simulated
  `web_search`. Nothing here establishes behaviour on real services or other failure
  modes.
- **One receiver in the reported run.** The four-arm and WP3 runs use a deterministic rule
  parser. A one-off LLM-receiver run
  ([`docs/LLM_RECEIVER_OBSERVATION.md`](LLM_RECEIVER_OBSERVATION.md)) reproduces every arm
  exactly except `raw_trajectory`, which changes the interpretation of one supporting claim
  but not of R1–R4; it is not byte-reproducible and is not part of the committed data.
- **Exact equality.** R2 is an equivalence statement within ±0.05 ΔSR, at N=30.
- **The value of attribution.** Auditability and accountability may justify the envelope
  even without a performance increment. That is a plausible reason to keep it; it is not
  measured here, and this note does not claim it.
- **WP3 under an LLM receiver.** It would need about 1,200 model calls per seed. The rules'
  decisions do not depend on the strategy model, but that is an argument, not a measurement.

## Implications

- **Keep the gate at the centre of the story.** It is the only component with a measured
  effect, it is receiver-independent, and it is what turns a harmful packet into a no-op.
- **Treat the envelope as a transport and accounting choice.** That is not the same as
  "useless": it carries attribution, provenance and a verifiable result type. But it should
  not be sold on performance grounds in this scenario.
- **Future protocol-layer claims need the right baseline.** A comparison against "no
  sharing" will always look good; the honest baseline for envelope claims is
  `plain_file_gated`.
- **Absorption should parse, not quote.** The one rule that failed is the one that assumes
  the absorbed prose stays in context. `retrieval` and `local_adapter_hint` are the
  mechanisms that pass.

## Status

This note is non-normative. It changes no wire behaviour, no schema, and no rule in
`run.py`; it records what the current rules measured, so that the result is not buried in
a table.
