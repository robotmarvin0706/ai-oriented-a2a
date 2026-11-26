# Protocol-layer value: what the selection experiment shows (non-normative)

> **Status: non-normative.** Summary of experiment 1 of
> [`docs/PREREG_SELECTION.md`](PREREG_SELECTION.md). The generated report with every table is
> [`experiments/selection/SELECTION_RESULTS.md`](../experiments/selection/SELECTION_RESULTS.md); if this
> file and the report disagree, the report governs. Nothing here changes `SPEC.md`, `schema/` or the
> WP3 rules in `experiments/timeout_recovery/run.py`.

## Why this file exists

[`docs/WP3_FINDINGS.md`](WP3_FINDINGS.md) explains that WP3 could not separate the envelope from the
host gate: `protocol − plain_file_gated` was 0 by construction, because no envelope field entered the
decision. The selection experiment measures a quantity that is not 0 by construction: a receiver that
uses envelope fields (`prior_outcomes`, `provenance`, its own attributed Absorption Results) to decide
which packets to absorb, against one that receives the same envelopes and ignores them. Content,
parser, gate and host are the same in both arms.

## Result (primary contrast, `naive_host`)

- Verdict (§12): **`supported: envelope-informed selection improves plateau SR by at least the margin`** (R1a).
- `D1 − C` (`history_selection − random_selection`) on `plateau_SR`: **+0.447, 95% CI [+0.401, +0.488]**
  over 160 runs (seeds 42, 7, 2026, 31337 × 40 runs × 200 tasks). Realised half-width 0.043, below the
  0.065 flag.
- V1 content-stub check: **pass** (`C − V1` = +0.143 [+0.107, +0.182]): absorbed content does reach the
  policy.
- V2 envelope-shuffle falsification: **pass** (`V2 − C` = +0.006 [−0.049, +0.059]): with envelope fields
  permuted across packets and content unchanged, informed selection falls back to the random arm, so the
  gain comes from the link between envelope and content.
- Harmful (dishonest-peer) packets absorbed on `naive_host`: 0 in A, B, C, D1, D2, V1 and 1 in V2, out of
  160 runs per arm.

## Reading it correctly

- **The host gate is still doing the safety work.** The dishonest peer's self-reports are good, so
  `history_selection` tries its packets more often than random selection does (263 harmful selections
  in 640 slots for D1, against 125 for C). Every one was rolled back by the isolation gate; D1 then
  recovers through attribution of its own Absorption Results (§7.2). The envelope speeds up finding the
  scarce effective packets; it does not replace the gate.
- **`claim_selection` (D2, secondary) is not misled in SR terms** (+0.463 [+0.419, +0.505]) for the same
  reason: the fabricated claims (0.85–0.95) rank harmful packets near the top, the gate rejects them, and
  the honest effective packets' claims are higher still in this pool. This is a property of this
  simulated pool and gate, not evidence that self-reported claims are trustworthy.
- **It costs tokens.** Over T1–T200, D1 spends about +106k tokens per run more than C (+30k up to its
  plateau), from reading envelopes at every slot (about 24k), more gate runs on candidates, and the
  absorbed block staying in context. `C − B`, the packaging cost of the protocol path, is about +34k per
  run.
- **`working_host` (secondary)**: every contrast is `equivalent within ±0.10 ΔSR`; a host that already
  recovers has almost nothing to gain from any packet, and no harmful packet was absorbed in any arm.
- **Scope**: one simulated tool (`web_search` timeout recovery), a deterministic rule-parser receiver
  (`deterministic-rule-host-v1`), synthetic peers, a 50-packet pool with 3 effective packets and 4
  absorption slots. An LLM receiver, other tools and other pool compositions are not covered.
- **Exchangeability gate (§3, §15.1): the cross-language part is met, third-party adoption is not.**
  `impls/go/` — a clean-room Go implementation (standard library only) written from the normative
  documents, not from this repository's Python code — passes `tests/conformance/` in external mode
  (`63 passed`, 0 failed, 0 skipped, including the 16-thread concurrency case) and reproduces the exact
  `history_selection` and `claim_selection` order of the Python runner on the frozen fixture
  `tests/conformance/fixtures/selection_pools.json` (8 parity tests). Limitation: the Go ranker uses a
  deterministic `packet.id` ascending tie-break where the runner uses a seeded RNG, so ties may be
  ordered differently; the scores and the eligibility rules are identical. Independent third-party
  adoption is still **not** met (0 external authors — the Go implementation lives in this repository and
  is maintained by the same maintainers), so this is not yet an externally adopted protocol.
  `experiments/selection/SELECTION_RESULTS.md` is a generated, frozen artifact of the run and is
  intentionally not edited.

## Next

Per §15 of the pre-registration: the second implementation is done for the cross-language part (see
above); next is the expiry experiment (`docs/PREREG_EXPIRY.md`, its own pre-registration and merge
before any run), then the budget experiment. Wiring `impls/go/` into CI (a Go toolchain job) would be a
separate PR.
