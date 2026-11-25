# LLM receiver observation (one-off, not byte-reproducible)

> **This is not part of the committed data.** The numbers below come from a single run
> against a hosted LLM on one day. The provider does not guarantee identical output at
> `temperature: 0`, so nothing here is under `datasets/`, no test asserts it, and a rerun
> may move by a few points. Treat it as an observation that bounds a claim, not as a
> result that can be reproduced byte for byte.

## What was run

- Date: 2026-11-25.
- Receiver: `llm:deepseek-chat` through an OpenAI-compatible endpoint, `temperature: 0`.
- Command: `python -m experiments.timeout_recovery.run --seeds 42 7 2026 --n 30 --model llm`.
- Cases and draws are the same as the committed four-arm run
  (`deterministic-rule-host-v1`), so the two receivers are compared per case on
  identical cases.
- Only the strategy model changed. `LLMStrategyModel` asks the model for a parameter
  object and parses the reply with the same `parse_recovery_params` the rule parser uses;
  the parameters then go through the same gate, isolation and rollback path.

## Result

ΔSR against the same host's `no_sharing`, N=30. Rule values are from the committed run;
LLM values are from the run above.

| seed | arm | rule parser ΔSR | LLM ΔSR | rule accepted | LLM accepted |
| ---: | --- | ---: | ---: | ---: | ---: |
| 42 | `raw_trajectory` | +0.094 [+0.033, +0.167] | **+0.306** [+0.200, +0.417] | 0.333 | 0.533 |
| 7 | `raw_trajectory` | +0.122 [+0.050, +0.211] | **+0.206** [+0.111, +0.306] | 0.333 | 0.467 |
| 2026 | `raw_trajectory` | +0.100 [+0.033, +0.183] | **+0.289** [+0.161, +0.417] | 0.233 | 0.467 |

Every other arm is identical to four decimals across the three seeds and both receivers:

| arm | seed 42 | seed 7 | seed 2026 |
| --- | ---: | ---: | ---: |
| `own_history` | +0.161 | +0.194 | +0.183 |
| `distilled_packet` | +0.578 | +0.517 | +0.522 |
| `mediocre_packet` | +0.011 | +0.000 | +0.000 |

Controls are identical too: the wrong packet is parsed 30/30, accepted 0/30 and rolled
back 30/30 on every seed; the injection packet is rejected by `content_policy:` 30/30 with
`model calls = 0`, so it never reaches the model; the ungated wrong packet costs the
working host −0.628 ΔSR and the gated path ends at +0.000. `judgement A` and `judgement B`
are `net_value: true` on all three seeds under both receivers.

## Stability

Seed 42 was run twice under the same conditions. `raw_trajectory` moved from +0.272
[+0.161, +0.389] to +0.306 [+0.200, +0.417], a ΔSR drift of 0.034; every other arm was
identical in both runs. The gap between receivers is 0.19–0.21 ΔSR on seeds 42 and 2026,
about six times that drift; on seed 7 it is 0.084, inside the noise, and the two confidence
intervals overlap heavily.

## Interpretation

- **Structured content makes the receiver irrelevant.** `distilled_packet` carries
  `content.recovery_policy`, so both receivers extract the same parameters and the
  outcome is identical to four decimals, across models, seeds and repeat runs. The
  reproducibility of the packet path is a property of the format, not of the receiver.
- **"Raw trajectories add little over `own_history`" is a property of the rule parser.**
  Under an LLM receiver the same arm is 1.7× to 3.3× higher and overtakes `own_history` on
  all three seeds. The ordering `distilled > raw > own_history` holds under the LLM;
  under the rule parser it is `distilled > own_history > raw`.
- **Distillation still wins under both receivers**, so the ranking that the project's main
  claim depends on is not affected. What changes is the size of the penalty for not
  distilling: about 6× under the rule parser, about 2× under the LLM.
- **The safety path is receiver-independent.** Content policy, isolation and rollback
  behave the same in both, which is expected: they run before and after the strategy
  model, not inside it.

## How to reproduce

An OpenAI-compatible endpoint is required:

    export A2A_TR_LLM_URL=https://api.deepseek.com/chat/completions
    export A2A_TR_LLM_MODEL=deepseek-chat
    export A2A_TR_LLM_KEY=...
    python -m experiments.timeout_recovery.run --seeds 42 7 2026 --n 30 --model llm

A plain run prints to stdout and writes nothing; do not add `--results`, `--summary-dir` or
`--data-dir`, which would overwrite the committed rule-model artifacts. The first line of
stdout states the strategy model, so an output file is self-describing. About 720 model
calls are made; the adapter has no retry, so one HTTP error ends the run.

## Not covered

- WP3 (R1–R4) was not run with an LLM receiver; it compares how the same experience
  reaches the host and does not depend on the strategy model for its decisions, but that
  is an argument, not a measurement.
- One model, one simulated tool, one day. The effect direction is consistent across three
  seeds; the magnitude is not established.
