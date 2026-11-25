# WP3 findings: the gate has value, the protocol-layer increment is not measurable

> Four seeds (42, 7, 2026, 31337) at N=30. Per-seed tables and the raw verdicts:
> [`experiments/timeout_recovery/WP3_RESULTS.md`](../experiments/timeout_recovery/WP3_RESULTS.md).
> The decision rules (R1–R4) were pre-registered in
> [`experiments/timeout_recovery/run.py`](../experiments/timeout_recovery/run.py) before
> any run and were not edited to fit the result. Reproduce with
> `python -m experiments.timeout_recovery.wp3 --seeds 42 7 2026 31337 --n 30 --results
> --summary-dir /tmp/wp3-summaries` (an explicit temporary path: the bare flag defaults to
> the committed directory).

## The claim

In the one scenario this project can currently measure, **the value is in the host's
absorption gate.** The protocol layer adds no measurable increment beyond it in this
experiment — but see the next section: the two arms share one parser, so a zero is what
the design produces, not something the measurement established. The gate result is the
strongest evidence the project has; the envelope result is a limitation of the design
until a non-SR endpoint is used.

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

`plain_file_gated` reads the packet's `content`, while `protocol` is charged the wrapped
envelope (markers plus the full packet), so the protocol arm costs **+264 to +630
tokens/case** more. The arms therefore differ in packaging cost and attribution; the
parameter path is shared, which is the problem below.

## Why R2 cannot be read as "no increment"

Both arms call `parse_recovery_params` on the same content and then the same deterministic
gate, so identical parameters follow, and a paired ΔSR of 0 is an identity rather than a
measurement. Replacing the strategy model with a stub that returns `{}` makes the arms
diverge immediately (−0.667, −0.667, −0.167 on seed 42), which shows the experiment detects
**parser differences**, not the envelope. R2 can therefore neither confirm nor refute a
protocol-layer increment; the honest statement is "this design cannot measure it".

## Findings

### 1. The gate has value (R1: pass on all four seeds)

On a host that already recovers (`working_host`, SR 0.911–0.944), the ungated wrong packet
costs **−0.628 / −0.578 / −0.556 / −0.606 ΔSR** (seeds 42/7/2026/31337). With the gate the same packet ends at
**+0.000** and rolls back in 30/30 cases. The gate is not free: on `distilled_packet` it
accepts 27, 26, 27 and 25 of 30 on the four seeds, because the remaining cases fail the
isolation threshold — roughly +0.05 ΔSR given up per seed in exchange for that protection.

### 2. The protocol layer adds no measurable increment (R2: `none` on all four seeds)

The paired `protocol − plain_file_gated` ΔSR on `distilled_packet` is **0.000** on both
hosts, with the 95% CI inside the pre-registered margin of **±0.05**. That is a CI-inclusion
rule, not a TOST (there is no test statistic and no p-value), and with a degenerate CI of
[0, 0] the margin says nothing about precision: the zero comes from the shared parser
above, not from the margin. A run whose arms genuinely differ would need a margin at or
above the N=30 noise floor (about 0.08) to read as equivalent.

The one measured difference is **attribution**: policy changes traceable to a packet id
with a matching Absorption Result are 1.0 for `protocol` and 0.0 for `plain_file_gated`.

### 3. The retrieval path is feasible only if the host drops the text (R3: `false`)

Nine of the ten feasibility checks pass (four token views plus ΔSR, on each of two hosts).
The failure is
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
- **WP3 under an LLM receiver.** It would need about 240 model calls per seed (about 1,200
  for four seeds). R1, R3 and R4 do not depend on the strategy model; R2 does, because its
  zero is produced by the shared parser.

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
