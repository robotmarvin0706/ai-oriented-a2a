# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The protocol version on `main` is **v0.4** (on-wire `"0.4"`; v0.4 receivers still accept `"0.3"`). Library and release tags may add a patch component.

## [Unreleased]

### Added

- `experiments/expiry/`: runner, analysis and generated results of experiment 2 (non-normative). It implements `docs/PREREG_EXPIRY.md` as merged in `56a47f3`, with no amendment and no change to any rule, N, δ or planning value: arms A `no_sharing`, C `expiry_blind`, D `expiry_enforced`, V1 `expiry_blind_stub`, V2 `expiry_random`; 4 seeds × 40 runs × 200 tasks on `naive_host`; paired bootstrap stratified by seed. The generated report `experiments/expiry/EXPIRY_RESULTS.md` (plus `summaries/runs.jsonl` and `summaries/summary.json`) gives the registered verdict `supported: enforcing machine-checkable expiry improves recovery-window SR by at least the margin` (R1a) with primary `D − C` on `recovery_window_SR` = **+0.167, 95% CI [+0.143, +0.192]** (half-width 0.024, below the 0.034 flag), V1 and V2 both pass, `D − C` on `pre_shift_SR` = −0.020 [−0.024, −0.017] and on `full_sequence_SR` = +0.015 [+0.010, +0.020]. Deterministic across `--jobs`; the CLI writes nothing to the repository by default.
- `docs/PROTOCOL_LAYER_VALUE.md`: new non-normative section "Experiment 2: expiry" summarising the generated report — exact verdict label, the primary `D − C` estimate with CI and the half-width against 0.034, the V1/V2 outcomes, `D − C` on `pre_shift_SR` and `full_sequence_SR`, the false-positive expiry cost, tokens and `expiry_overhead_tokens`, the 25%/50% silent-shift and benign-bump planning values with the observed rates and the bound from the detected-shift share, and the single-project and simulated-tool caveats.
- `docs/PREREG_EXPIRY.md`: pre-registration of experiment 2 (non-normative): whether enforcing a machine-checkable `expiry` (a non-normative `tool_version_neq` convention inside the existing open `expiry` object; no schema change) lets a receiver lose less success rate after a simulated tool regime shift. Imperfect signal with 25% silent shifts and 50% benign version bumps, primary contrast `expiry_enforced − expiry_blind` on `recovery_window_SR`, one margin δ = 0.08, 4 seeds × 40 runs × 200 tasks with a power calculation, a content-stub sanity check and a random-expiry falsification check. No runs before it is merged.
- CI: new `go-second-impl` job in `.github/workflows/validate.yml` builds `impls/go/` and runs the §15.1 external conformance and envelope selection parity gates.
- `impls/go/`: a minimal clean-room Go implementation (standard library only) — a `serve` receiver for `POST /a2a/message` and a `rank` envelope ranker — written from SPEC.md, `schema/`, TRANSPORT, DATA_POLICY, VERSIONING, COMPLIANCE and PREREG_SELECTION §7, without reading or importing `ai_oriented_a2a/` or `second_impl/`. It passes `tests/conformance/` in external mode (`63 passed`, 0 failed, 0 skipped, including the 16-thread concurrency case), and `rank` matches the Python runner's `history_selection` / `claim_selection` order on `tests/conformance/fixtures/selection_pools.json`. Not wired into CI; maintained by the same maintainers, so this is cross-language interoperability, not third-party adoption.
- `tests/conformance/test_selection_parity.py` and `tests/conformance/fixtures/selection_pools.json`: 8 parity tests comparing the Go `rank` subcommand with `experiments.selection.sim` (`history_score` / `claim_eligible`) on a frozen pool, with a deterministic `packet.id` ascending tie-break; skipped when `go` is absent and `A2A_GO_BIN` is unset.
- `tests/conformance/test_robustness.py`: the 16-thread concurrency case now also runs for `external` peers started via `A2A_PEER_CMD` (still not for `A2A_PEER_URL`-only peers).
- `experiments/selection/`: runner, analysis and generated results of the packet-selection experiment, implementing `docs/PREREG_SELECTION.md` as merged (75acb59) with no amendment. Primary contrast `D1 − C` on `naive_host`: +0.447, 95% CI [+0.401, +0.488] over 160 runs, verdict `supported: envelope-informed selection improves plateau SR by at least the margin`; V1 and V2 pass. Writes nothing by default (`--results`, `--summary-dir`). `experiments/timeout_recovery/` is unchanged.
- `docs/PROTOCOL_LAYER_VALUE.md`: non-normative summary of the selection result, its caveats (the host gate still rejects every harmful packet; token cost) and the exchangeability gate, which is not met.
- `docs/PREREG_SELECTION.md`: pre-registration of the envelope-informed packet-selection experiment (non-normative). Primary contrast `history_selection − random_selection` on plateau SR, one margin δ = 0.10, a scarce pool (3 effective packets in 50, 4 absorption slots) so the endpoint cannot saturate, 4 seeds × 40 runs × 200 tasks with a power calculation, an envelope-shuffle falsification check and a content-stub sanity check, noisy `prior_outcomes` and a dishonest peer. No runs before it is merged; the WP3 rules in `experiments/timeout_recovery/run.py` stay frozen.

## [0.4.2] - 2026-11-25

Library patch release, built from tag `v0.4.2`. The protocol version is unchanged (v0.4, on-wire `"0.4"`) and the wire format does not change: `schema/` is identical to 0.4.1 and `SPEC.md` gains only the RFC 2119/8174 boilerplate. Security fixes and documentation corrections: unattributable packets are rejected, redirects are refused, MCP output is sanitized, an over-long version segment and a non-result callback no longer produce a 500-shaped reply. Collects #61, #63 and #64.

Published to PyPI on 2026-11-25, built from tag `v0.4.2`: https://pypi.org/project/ai-oriented-a2a/0.4.2/
Also published to TestPyPI the same day: https://test.pypi.org/project/ai-oriented-a2a/0.4.2/

### Changed

- `ai_oriented_a2a.transport` and `second_impl` client: redirects are refused. urllib follows a 303 by re-issuing the request as a GET and returning the new body, so a peer could point the client at an internal URL and hand its body back as the "reply", bypassing any allowlist. A 3xx now raises `TransportError` and only `http`/`https` URLs are accepted (audit P1-7).
- `mcp_a2a`: returned packets are whitelisted. `absorption` is reduced to its `method` label, so `absorption.hint` — sender text the §2.1 content scan never sees — no longer reaches the host; the metadata fields (`absorption`, `provenance`, `verification_claim`) are now scanned for directives like `content`, and a packet that fails is blocked with a `content_policy:` reason. Replies from `send_packet` and `report_absorption` carry `untrusted: true` (audit P1-9).
- Docs, normative level: `SPEC.md` now states that RFC 2119/8174 keywords apply; `docs/SECURITY.md`, `docs/ABSORPTION_STATE.md` and `IMPLEMENTATION.md` say in their first paragraph that they are non-normative for the wire and that the SPEC wording governs where they restate it; `docs/DATA_POLICY.md` marks which sections are normative (only the forbidden-directive patterns) and which are corpus guidance.
- `docs/VERSIONING.md`: the forward-compatibility rule is now conditional — a receiver that does **not** schema-validate MUST ignore unknown OPTIONAL fields; one that does schema-validate rejects them, and both behaviours are conformant. Removes the contradiction with `additionalProperties: false` (audit P1-1).
- `experiments/timeout_recovery/run.py` and `wp3.py`: the CLI now prints `strategy model: <name>` as the first line of stdout, before the run. A smoke run's output file states which strategy model produced it (`deterministic-rule-host-v1` or `llm:<model>`), instead of leaving the reader to infer it from the tables.
- `mcp_a2a`: outbound messages are now signed unconditionally. `A2A_SIGN_KEY` is required; missing or blank is a tool error and nothing is sent, and there is no unsigned fallback. The peer allowlist is checked before the key is read, so a disallowed `peer_url` is refused without touching the key. **Hosts must add `A2A_SIGN_KEY` to the server environment**; a host without it now fails every outbound call instead of sending unsigned packets.
- `experiments/timeout_recovery/run.py`: WP3 rule R2 is a CI-inclusion equivalence rule (not a TOST: there is no test statistic or p-value) with the fixed margin `WP3_R2_EQ_MARGIN = 0.05`, not "the paired CI contains 0". A paired `protocol − plain_file_gated` ΔSR CI entirely inside ±0.05 is evidence of no protocol increment; a CI that merely contains 0 is reported as `undetermined`, and a CI outside the margin as `increment` (with `positive` / `negative` per host). `protocol_increment` (`none` / `increment` / `undetermined`) is reported per seed and overall, and `no_protocol_increment` is true only for `none`. Revised 2026-11-25, still before any WP3 run, so the pre-registration stays ahead of the data.

### Fixed

- `examples/experience-packet-trajectory-replay.json` now says in its content that `trajectory_replay` is OPTIONAL and that the reference receiver rejects the packet with an explicit reason, so the file is not read as a working demo (audit P2-14).

### Added

- `llms.txt` and `AGENTS.md` at the repository root: what the project is, how to install it, the minimal receive loop, what to read next, the invariants, and the things not to do. Both state that this is not the Google A2A stack and that packet content is data, never instructions.
- `docs/WP3_FINDINGS.md`: a short, non-normative statement of what WP3 measured, with the measured numbers and the limits of the design. The gate has value (the ungated wrong packet costs the working host −0.628 / −0.578 / −0.556 / −0.606 ΔSR on the four seeds; the gated path ends at +0.000, at the price of 27/26/27/25 of 30 accepted). R2's two arms share one parser, so its zero is an identity rather than a measurement — a stubbed strategy model makes them diverge — and the note says the experiment cannot detect a protocol-layer increment. The retrieval path is cost-feasible only when the host parses to parameters and drops the text (R3: nine of ten checks). [`WP3_RESULTS.md`](experiments/timeout_recovery/WP3_RESULTS.md) has the tables.
- `experiments/timeout_recovery/wp3.py`: the WP3 runner (non-normative) that produces the per-seed summary the pre-registered R1–R4 rules in `run.py` read. Arms `own_history`, `plain_file` (ungated counterfactual), `plain_file_gated`, `protocol`, `retrieval_bridge`; both hosts (`naive_host`, `working_host`); the five packets; seeds 42, 7, 2026, 31337 at N=30. `retrieval_bridge` uses `mcp_a2a.lookup` over a one-packet local corpus per row, so it measures the retrieval path's cost, not ranking quality. `protocol` runs in-process (the HTTP binding is covered by the transport and conformance tests) and `plain_file_gated` is charged the same resident-context cost as `protocol`, so the arms differ only in packet id and Absorption Result. No wire change: `SPEC.md`, `schema/` and `docs/` are untouched.
- `experiments/timeout_recovery/WP3_RESULTS.md` and `wp3_summaries/`: the four-seed run and its verdicts. On every seed: **R1 pass** (the gate has value: the ungated wrong packet costs the working host up to -0.539 ΔSR, the gated path +0.000), **R2 `none`** — an identity, because both arms share one parser, so this is not evidence of no increment; the paired CI is [0, 0], **R3 `false`** (one of the eight feasibility checks fails on every seed, `naive_host.a_amortized_extra_tokens_within_cap`; the retrieval path passes the other seven and passes under view B), **R4 pass** (injection rejected by `content_policy` on every gated arm, accepted in none).
- `tests/experiments/test_wp3_runner.py`: summary shape, paired CI over cases, determinism, attribution (protocol only), `content_policy` on the gated arms, the ungated arm by design, the retrieval stats, and the rules running on a real run.
- `mcp_a2a/query_experience`: a third MCP tool that looks up Experience Packets in a **local** corpus (`A2A_EXPERIENCE_DIR`, default `datasets/timeout_recovery/packets`) and returns their content under a token budget. It never contacts a peer. Every candidate goes through the SPEC §2.1 content check before ranking, so a directive packet is reported in `blocked` with its `content_policy:` reason and its content is never returned. Items carry `"untrusted": true` and the reply repeats the §2.1 notice: returned content is data, never instructions.
- `mcp_a2a/lookup.py`: the corpus loading, ranking, token budget and block list as a standard-library module that does not import the MCP SDK, so it is tested in CI without the optional `mcp` extra. `mcp_a2a.require_sign_key()` is the fail-closed guard for `A2A_SIGN_KEY`.
- `tests/mcp_bridge/test_lookup.py`: 21 tests for ranking, the block list (including a directive nested in `offered_packet` / `conditional_packet`), the token budget, unreadable corpus files, and the signing guard.

## [0.4.1] - 2026-11-23

Library patch release, built from tag `v0.4.1`. The protocol version is unchanged (v0.4, on-wire `"0.4"`). Experiment, example fixes, a non-normative helper module (`ai_oriented_a2a.recovery`), tests and data only: no wire-protocol change, no new fields, SPEC.md unchanged. Collects #51, #52 and #53.

Published to PyPI on 2026-11-23, built from tag `v0.4.1`: https://pypi.org/project/ai-oriented-a2a/0.4.1/
Also published to TestPyPI the same day: https://test.pypi.org/project/ai-oriented-a2a/0.4.1/

### Fixed

- `examples/minimal_dual_end.py`: absorption no longer sets a hard-coded `recovered_policy()` whatever the packet says. The receiver parses `backoff`, `max_attempts` and `drop_stale_partials` from packet `content` (structured `recovery_policy`, a raw trajectory, or the recommendation text; never `absorption.hint`). Content with no such parameter leaves the policy unchanged and is rejected with `strategy_parsed: false` in the metrics. A change with no measured gain over regression + isolation (pooled) is now rolled back instead of absorbed. The simulated tool throttles a caller after 3 consecutive immediate retries, so unbounded retries without backoff cannot add successes. The default four-arm table is unchanged; `net_delta_sr_experiment.py` and `gossip_sim.py`, which reuse this receiver, now give different numbers (for example seed 42 Stage 1: distilled ΔSR +0.356 instead of +0.402).
- `examples/network_dual_end.py`: the simulated regression is seeded with the first 8 bytes of sha256(packet id) (`stable_seed`) instead of `hash(id)`, so the result is the same in every process. The reference receiver now returns `absorbed` for `examples/experience-packet.json`, and the tests that accepted any of `absorbed` / `rejected` / `isolated` for it assert `absorbed` (in the conformance copy, only for the `reference` implementation). Removed from `docs/KNOWN_GAPS.md` (remaining entries renumbered).

### Changed

- `experiments/timeout_recovery/run.py`: a plain run writes nothing and prints the tables to stdout. Files are written only with `--results [PATH]`, `--summary-dir [DIR]` or `--data-dir [DIR]` (a bare flag uses the committed path), so a trial run can no longer overwrite `RESULTS.md`, the summaries or the datasets and break the data tests. `--no-write` is gone (it is now the default). Regenerate the committed files with `--results --summary-dir --data-dir`.
- MCP bridge: the requirement is `mcp>=2,<3` (`mcp_a2a.MCP_REQUIREMENT`, same as the `mcp` extra). `tests/mcp_bridge/test_bridge.py` is skipped at module level with the reason when `mcp` is missing, older than 2 (1.x has no `Client` / `MCPServer`), or Python < 3.10, instead of failing at collection; `import mcp_a2a.server` raises a clear `ImportError` in that case.

- CI: the `docs-and-examples` job in `.github/workflows/validate.yml` now also runs `tests/experiments/` (#52).

### Added

- `experiments/timeout_recovery/` fair-comparison changes (WP1; no wire change, no new message type or field):
  - `own_history` no longer uses a fixed rule (backoff 200 ms, 3 attempts). It searches the same parameter space a distilled packet states (`drop_stale_partials`, backoff off or 100/200/400/800 ms, `max_attempts` 2–6; the peer's 400 ms / 4 / drop is inside it) on its own paired exploration episodes, stopping at 3000 tool-call tokens, and the pick goes through the same isolation gate. Its search tool calls are counted as its cost.
  - New arm `mediocre_packet`: a distilled packet from a peer only slightly better than naive (exponential from 50 ms, 3 attempts, keep stale partials), judged by the same rule for information.
  - The wrong packet is reported on a naive host and on a working host (the peer policy configured locally), with and without the gate, so "no effect" and "harmful" are separate.
  - Tokens are reported in two views, A (absorbed text in context on every episode, as before) and B (parsed parameters only); the fixed decision rule is applied to both and both verdicts are in `RESULTS.md`. Thresholds unchanged.
  - The host answers a message that is neither a packet nor a negotiation message with a rejected Absorption Result (`unsupported_message:` for a profile or a pushed Absorption Result, `schema_invalid:` otherwise) instead of a bare `{"ack": true}`.
  - `parse_recovery_params` and `policy_from_params` moved to `ai_oriented_a2a.recovery` (from `experiments/timeout_recovery/strategy.py`, now removed); `examples/minimal_dual_end.py` and the experiment both import them from there, so `examples/` no longer imports `experiments/`.
  - `RESULTS.md` and the summaries add two information-only tables that do not enter the verdict: amortization sensitivity (one-time cost vs evaluation-episode extra; with one-time costs shared across N=30 cases, distilled's extra-token ratio is 6.79–7.56 under view A, over the 1.5 cap, and 1.26–1.49 under view B), and the policies own_history's search picked (the peer policy in 0/30 cases on every seed).
  - Rerun of seeds 42, 7, 2026 (N=30): verdict under both views is now **net value** (it was no net value under A). The change comes from `own_history` paying for its search (extra tokens about 2700–3000 vs 716–747 before), while its ΔSR rose from +0.04–+0.11 to +0.16–+0.19; distilled is unchanged (+0.52–+0.58). The mediocre arm gains +0.00–+0.01 at about 1000–1150 extra tokens (A) and fails the rule on every seed.
- `experiments/timeout_recovery/run.py`: WP3 decision rules R1–R4 written before any WP3 run (`wp3_r1_gate_value`, `wp3_r2_protocol_increment`, `wp3_r3_retrieval_feasible`, `wp3_r4_safety`, `wp3_judge`, `wp3_judge_overall`, plus unjudged auxiliary metrics), tested on built summaries in `tests/experiments/test_wp3_rules.py`. No WP3 runner or arms yet; `retrieval_bridge` waits for WP2, and R3 reports `not_run` until then. R4 (injection rejected by content_policy in all n cases, none accepted) covers only the gated arms `plain_file_gated`, `protocol` and `retrieval_bridge` (`WP3_R4_ARMS`); the ungated `plain_file` arm is excluded by design.
- `experiments/timeout_recovery/` (non-normative): a minimal host with a tool-call loop against a simulated `web_search` tool, absorption through the library's public interfaces (`check_incoming`, `content_policy_rejection`, `NegotiationGuard`, `wrap_untrusted`, `transport.guard_result`, `transport.make_server`), a pluggable strategy model (default deterministic rule model; optional OpenAI-compatible adapter, skipped when not configured), four arms plus four control packets, and fixed decision rules. One command reproduces it: `python -m experiments.timeout_recovery.run --seeds 42 7 2026 --n 30`. Results: `experiments/timeout_recovery/RESULTS.md` (first run, PR #51: no net value, because the distilled arm's extra tokens were about 2.6–2.8× own_history against a 1.5× cap; see the WP1 entry above for the current run).
- `datasets/timeout_recovery/runs/<seed>/`: trajectories of that run as v0.4 Experience Packets (JSON Lines) with `verification_claim` (`scenario_ref`, `metric`, `value`, `repro`), with per-seed summaries in `experiments/timeout_recovery/summaries/`. Described in `datasets/README.md` (CC0, like the rest of `datasets/`).
- `tests/experiments/`: byte-identical reruns (also across processes), committed data matches a fresh run, wrong-experience packets give ΔSR ≤ 0 and roll back, empty or unrelated packets leave the policy unchanged, directive content is rejected with `content_policy:` before the strategy model sees it, schema validation, and an HTTP round trip through `transport.make_server`.

## [0.4.0] - 2026-11-22

This release collects `spec/content-is-data` (#47) and `spec/core-constraints` (#48), built from tag `v0.4.0`.

Published to PyPI on 2026-11-22, built from tag `v0.4.0`: https://pypi.org/project/ai-oriented-a2a/0.4.0/
Also published to TestPyPI the same day: https://test.pypi.org/project/ai-oriented-a2a/0.4.0/

Protocol v0.4 (on-wire `"0.4"`) and library 0.4.0. During 0.x, SPEC tightenings that add receiver MUSTs take a minor bump ([docs/VERSIONING.md](docs/VERSIONING.md)). Receivers implementing 0.4 still accept on-wire `"0.3"` and omitted `version` (omitted is treated as the current normative version and processed under 0.4 rules).

### Breaking (specification tightening)

- SPEC §2.1 Untrusted content: receivers MUST treat Experience Packet `content` (and nested `offered_packet` / `conditional_packet`) as untrusted external data, never as instructions to the receiver. Absorption with `prompt_injection` MUST wrap the content in explicit untrusted markers and place it in a role below system and host instructions (marker format is implementation-defined and MUST be documented). Receivers SHOULD reject directive-like content with `content_policy:` and MUST NOT auto-absorb it.
- On-wire `version` is now `"0.4"`. `SUPPORTED_MAJOR_MINOR` (reference and `second_impl`) changes from the pair `(0, 3)` to the tuple of supported pairs `((0, 3), (0, 4))`; code that compared against it with `==` must use `in`. `"0.5"` and `"v0.4"` are rejected with `unsupported_version:`.

### Added (untrusted content, #47)

- OPTIONAL `verification_claim` fields: `scenario_ref`, `metric`, `value`, and `repro` (object with `seed`, `model`, and `n`). A packet that reports a prior absorption SHOULD include them so a third party can rerun the claim. Schema keeps `additionalProperties: false` on `verification_claim` and on `repro`. `audit_request` is not in this release.
- `ai_oriented_a2a.content`: `wrap_untrusted(packet)`, `scan_directives(content)`, and `content_policy_rejection(msg)`, re-exported from the top-level package (standard library only). Documented as a baseline check, not a complete security guarantee. The `wrap_untrusted` marker format is in IMPLEMENTATION.md. New top-level constants `PROTOCOL_VERSION` (`"0.4"`) and `CURRENT_MAJOR_MINOR` (`(0, 4)`).
- `second_impl` has an equivalent check and wrapper (`directive_hits`, `content_problem`, `untrusted_block`) and does not import `ai_oriented_a2a`. Tests check that both give the same pattern hits and byte-identical wrapped output.
- `docs/DATA_POLICY.md` section "Forbidden directive patterns" is normative (the rest of that file stays advisory): `ignore_prior_instructions`, `modify_constraints`, `claim_system_or_host`, `disable_verification`, `bypass_isolation`, `exfiltrate_or_override_policy`. Synthetic corpus packets MUST NOT carry `verification_claim.value`.
- Two CC0 synthetic counter-example packets under `datasets/content_policy/` (`pkt-synth-inject-001`, `pkt-synth-inject-002`) and the expected rejection.
- Reserved reason prefix `content_policy:` in TRANSPORT.md.
- Conformance cases in `tests/conformance/test_untrusted_content.py` (reference, `second_impl`, external) and unit / parity tests in `tests/interop/test_content_policy.py`.

### Changed (untrusted content, #47)

- Reference receivers: `examples/network_dual_end.py` rejects a packet, or a negotiation message whose nested packet, matches a forbidden directive pattern with `content_policy:` before the method check. `examples/minimal_dual_end.py` rejects such packets without touching local state, and its `prompt_injection` path now injects `wrap_untrusted(packet)` instead of the sender's `absorption.hint` (the four-arm table is unchanged; the `prompt_after` metric shows a short label for the wrapped block).
- `second_impl` `respond` rejects directive-like content with `content_policy:` before its isolate / `unsupported method` / `negotiation_unsupported:` decisions.
- Examples that carried `"version": "0.3"` now carry `"0.4"`; the signed example `experience-packet-with-security.json` was re-signed with the documented key `demo`.
- Docs (SPEC, VERSIONING, TRANSPORT, COMPLIANCE, SECURITY, IMPLEMENTATION, README, `second_impl/` and `mcp_a2a/` READMEs) say v0.4. Unknown-version examples use `0.5` instead of `0.4`.

### Added (core constraints, #48)

From `spec/core-constraints`. The fields are OPTIONAL; the new MUSTs apply only when a receiver has `core_constraints` or a `negotiation_id` passes the round cap, so they ride on the same 0.x minor bump ([docs/VERSIONING.md](docs/VERSIONING.md)) and need no further version change.

- OPTIONAL Agent Profile field `core_constraints` (string array). Existing `constraints` is unchanged. A propose or counter whose `target_constraint` matches the receiver's `core_constraints` (exact string) MUST be rejected with `immutable_constraint:`. Core constraints MUST NOT be changed by any protocol message; only the host may change them off-protocol.
- OPTIONAL `negotiation_id` on all four negotiation messages (non-empty string), generated by the initiator and copied on every accept, reject, and counter in the chain. SHOULD be present. Without it, a receiver MAY treat the exchange as a single round and MUST NOT count different `proposal_id`s together (necessary because a counter MUST already use a new `proposal_id`, so counting rounds by `proposal_id` is always 1). OPTIONAL `target_constraint` on counter, so a counter can be checked against core constraints.
- Implementations SHOULD cap rounds per `negotiation_id` (default 3) and MUST reject overflows with `negotiation_limit:`. Every message with that id counts, including rejected ones. They SHOULD also rate-limit per peer; the numeric limit is implementation-defined.
- Order for negotiation messages: `content_policy:` (SPEC §2.1) first, then `negotiation_limit:`, then `immutable_constraint:`, then anything else such as `negotiation_unsupported:` (SPEC §3.0 says why).
- Reserved reason prefixes `immutable_constraint:` and `negotiation_limit:` in TRANSPORT.md.
- SPEC MAY: a receiver may isolate a peer based on local history. The record format is not in the protocol. No `peer_history` or `sandbox_required` field.
- `ai_oriented_a2a.negotiation`: `NegotiationGuard` (round cap, optional per-peer rate limit, bounded table), `immutable_constraint_rejection`, and `DEFAULT_MAX_NEGOTIATION_ROUNDS = 3`, re-exported from the top-level package. `second_impl` has its own `NegotiationLimits`, `immutable_problem`, and `DEFAULT_ROUND_CAP = 3`, and still does not import `ai_oriented_a2a`.
- Conformance cases in `tests/conformance/test_core_constraints.py` (reference, `second_impl`, external; the conftest `start_receiver` takes `core_constraints`, passed to external peers as `--core-constraint` or described by `A2A_PEER_CORE_CONSTRAINTS`) and unit / parity tests in `tests/interop/test_negotiation_limits.py`.

### Changed (core constraints, #48)

- `examples/network_dual_end.py` and `second_impl` run the round cap and core-constraint check on negotiation messages after `content_policy:` and before `negotiation_unsupported:`. New flag `--core-constraint TEXT` (repeatable) on both receivers; `second_impl serve` also has `--max-rounds`. The reference profile lists `core_constraints` when any are set.
- Examples: `agent-profile-with-identity.json` carries `core_constraints`; `negotiation-propose-conditional.json` carries `negotiation_id`.

## [0.3.3] - 2026-11-21

Library and documentation release. The protocol version is unchanged (v0.3, on-wire `"0.3"`).

Published to PyPI on 2026-11-21, built from tag `v0.3.3`: https://pypi.org/project/ai-oriented-a2a/0.3.3/
Also published to TestPyPI the same day: https://test.pypi.org/project/ai-oriented-a2a/0.3.3/

### Fixed

- Non-finite numeric timestamps (`NaN`, `Infinity`, `-Infinity`): `parse_timestamp` and `check_replay` reject them with `replay_or_stale: invalid timestamp`. `second_impl` `ReplayWindow` does the same, so the two implementations agree. Previously `json.loads` accepted `NaN`, the window check passed (`abs(now - nan) > window` is never true), and the reference raised `ValueError` on `int(parsed)` while `second_impl` accepted the message. Integers too large for a float are rejected the same way.
- Replay cache updates (`any(...)` duplicate check, insert, and `_evict`) run under one `threading.Lock` bound to the seen set (new `ReplayCache`), so a `ThreadingHTTPServer` no longer fails with `Set changed size during iteration` under concurrent requests. `check_replay` / `check_incoming` still accept a bare set (optionally with a shared `lock=`).
- `A2ARequestHandler.seen_nonces` no longer defaults to one class-level `set()` shared by every subclass that does not override it. `make_handler` injects a fresh `ReplayCache` per handler class, shared by all request threads; a bare set assigned by a subclass is wrapped on first use.
- `make_server` (reference and `second_impl`) listens with a backlog of 128 instead of socketserver's 5, so a burst of 16 concurrent peers no longer sees connection resets.
- `examples/network_dual_end.py` answers a negotiation message with a legal rejected Absorption Result (`negotiation_unsupported:`, `packet_ref` = `offered_packet.id` or `"unknown"`) instead of `{"ack": True}`. Closes KNOWN_GAPS #1.

### Changed

- `do_POST` in `ai_oriented_a2a.transport` (and `second_impl`) wraps `check_incoming` and the message callback in one `try`. Unexpected exceptions return HTTP 500 and `internal_error: message handler failed`, with no traceback, path, or exception text.
- Request bodies are capped at `MAX_BODY_BYTES` (1_048_576). Missing, invalid, or too-large `Content-Length` returns HTTP 413 and a rejected Result whose reason starts with `payload_too_large:`, decided before the body is read. Prefix registered in TRANSPORT.md.
- Package docstring no longer says "no HTTP server". TRANSPORT.md says the example uses `ai_oriented_a2a.transport`. README links to SPEC, CHANGELOG, and docs use absolute GitHub URLs so they work on the PyPI project page.
- New conformance cases in `tests/conformance/test_robustness.py` (both implementations): non-finite timestamps, the 413 limit, negotiation rejection, and 16 concurrent requests against a threaded server.

## [0.3.2] - 2026-11-14

Library and documentation release. The protocol version is unchanged (v0.3, on-wire `"0.3"`). This is the first version published to PyPI, built from tag `v0.3.2`.

Published to PyPI on 2026-11-14: https://pypi.org/project/ai-oriented-a2a/0.3.2/
Also published to TestPyPI the same day: https://test.pypi.org/project/ai-oriented-a2a/0.3.2/

### Breaking

- **BREAKING:** String `timestamp` values MUST be ISO-8601 **with a timezone**. All 7 `timestamp` definitions in `schema/` now restrict the string form to `YYYY-MM-DDTHH:MM:SS`, an optional 3- or 6-digit fraction, and `Z` or `±HH:MM`. Messages that used other strings now fail schema validation, for example `"1731480000"` (numeric string) and `"2026-11-14T08:00:00"` (no timezone); also date-only, space separator, lowercase `t`/`z`, `+0800`, and 1- or 9-digit fractions. Numeric (JSON number) timestamps are unchanged. The pattern does not check calendar validity (`2026-02-30` passes the schema and then fails timestamp parsing). `ai_oriented_a2a.parse_timestamp` still treats naive strings as UTC as compatibility behaviour only; strict receivers MAY reject them with `schema_invalid:`.

### Changed

- Replay window (behaviour tightening): `check_replay` now treats a nonce as a duplicate whatever its `timestamp`, so the same nonce with a different timestamp is rejected with `replay_or_stale: duplicate nonce`. Previously the key was `(nonce, int(timestamp))`. Matches SECURITY.md and `second_impl/`.
- Replay set eviction: past `SEEN_MAX` (1000) entries the seen set is trimmed to `SEEN_TRIM_TO` (800) by dropping expired entries first, then the oldest timestamps, instead of arbitrary `set.pop()` entries. `seen` is still a plain `set` of `(nonce, str(int(timestamp)))`. Both constants live in `ai_oriented_a2a.protocol`; the replay-after-eviction caveat and smaller-window advice are in SECURITY.md and TRANSPORT.md.
- `examples/network_dual_end.py`: `A2AHandler` now subclasses `ai_oriented_a2a.transport.A2ARequestHandler`, and pushes and sends through `ai_oriented_a2a.transport.send_message`. Its positional `send_message(url, payload, sign_key=None)` wrapper is kept for existing callers but is deprecated and will be removed; new code should use `ai_oriented_a2a.transport.send_message` with keyword arguments.
- `docs/KNOWN_GAPS.md`: removed the replay-key and `set.pop()` eviction entries (fixed here) and renumbered the rest.
- `docs/RELEASING.md`: the first PyPI upload is `0.3.2` from tag `v0.3.2`; `0.3.1` is not uploaded. `pyproject.toml` `Release` URL points at `v0.3.2`.

### Added

- `ai_oriented_a2a.transport` (stdlib only, not re-exported; `import ai_oriented_a2a` does not load `http.server`): `send_message(url, message, *, timeout=30.0, sign_key=None, add_replay_fields=False)`, `TransportError`, `A2ARequestHandler`, `make_handler`, `make_server`, `serve`, and `guard_result` (the absorption hard gate on replies).
- Reserved reason prefix `internal_error:` (TRANSPORT.md). The reference handler answers a handler exception with HTTP 500 and a valid rejected Absorption Result, reason `internal_error: message handler failed`, with no exception text, traceback, or path.
- Tests: `tests/interop/test_timestamp_replay.py` and `tests/interop/test_transport.py`.

### Also in this release (merged after 0.3.1)

- `docs/DATA_POLICY.md` (non-normative): redaction, `provenance.source_ref` prefixes (`synthetic:`, `local_run:`, `peer_absorbed:`), 3-hop chain limit with `content.chain_truncated`, required `expected_effect` + `replay_hint`, no numeric uplift in synthetic data, CC0 for corpora. SPEC and schemas are unchanged; `provenance` stays OPTIONAL on the wire.
- `datasets/timeout_recovery/`: 8 synthetic Experience Packets and 2 Absorption Results for the tool-call timeout / failure-recovery task, under CC0 1.0 (`datasets/LICENSE`).
- `tests/interop/test_datasets.py`: CI checks for `datasets/**` (schema, legal prefix, verification claim, chain length, no numeric uplift). The `docs-and-examples` workflow also requires `docs/DATA_POLICY.md` and `datasets/README.md`.
- Prepared PyPI packaging and manual publish workflow: `pyproject.toml` adds `Repository` / `Changelog` URLs and `license-files`, and drops the legacy MIT license classifier (SPDX `license = "MIT"` already set). New manual-only `.github/workflows/publish.yml` (TestPyPI or PyPI, Trusted Publishing by default) and `docs/RELEASING.md`. README install section now leads with `pip install ai-oriented-a2a`. That packaging PR did not bump the version.
- `second_impl/`: an independent minimal implementation (stdlib + optional `jsonschema`, Python 3.8+) written from SPEC and TRANSPORT, with a `send` / `serve` CLI. It does not import `ai_oriented_a2a` or `examples/`. Check order: version, hard gate, schema (on by default, `--no-schema`), signature, replay. Its receiver never returns `absorbed`: packets are `isolated` (`isolated: no local regression available`) and negotiation messages are rejected with `negotiation_unsupported:`. Interoperates with `examples/network_dual_end.py` in both directions.
- `mcp_a2a/`: one-way MCP bridge (official MCP Python SDK 2.x, `MCPServer`) with `send_packet` and `report_absorption` tools that return the peer's reply unchanged and raise tool errors on local, transport, or protocol failures. New optional extra `[mcp]` (`mcp>=2,<3`, Python ≥ 3.10); the core package still requires Python ≥ 3.8 and has no required dependencies.
- `docs/TRANSPORT.md`: registers reserved `reason` prefixes `schema_invalid:` and `negotiation_unsupported:` (and the informative `isolated:`) alongside `unsupported_version:` and `replay_or_stale:`. These are reason-string conventions, not new fields; schemas are unchanged.
- `docs/COMPLIANCE.md`, `docs/KNOWN_GAPS.md`, and `tests/conformance/` (the 12 HTTP cases, parametrized over the reference, `second_impl`, and an external implementation via `A2A_PEER_CMD` / `A2A_PEER_URL`). New `tests/interop/test_second_impl.py` and `tests/interop/test_cross_impl.py`.
- CI: `docs-and-examples` also runs `tests/conformance/` and requires `docs/COMPLIANCE.md` and `docs/KNOWN_GAPS.md`. New `mcp-bridge` job (not a required check).

## [0.3.1] - 2026-11-13

Library and documentation release. The protocol version is unchanged (v0.3). The tightenings in this release only reject previously-malformed messages, or messages that omit required fields when an opt-in check (the replay window) is enabled. Messages that were valid under v0.3 and that do not enable those checks remain valid.

### Changed

- Replay window: when a receiver has enabled the window, both `nonce` and `timestamp` MUST be present and valid; missing either is rejected with `replay_or_stale`. When the window is off, both fields remain optional. (#34)
- Schema: `identity`, when present, now requires `fingerprint`. An empty `identity` object `{}` is no longer valid. (#34)
- Schema: `reason` MUST NOT be whitespace-only (same pattern as `note`). (#34)
- SPEC.md: `reason` and `note`, when present, MUST be non-empty and MUST NOT be only whitespace, matching the schemas. (#35)
- Receivers MUST check `version` before full schema validation. Unknown versions (including `"v0.3"`) return a valid Absorption Result (`rejected` + `unsupported_version`), not a schema-pattern error. Documented in TRANSPORT, VERSIONING, and SECURITY. (#34, this release)
- `validate()` is documented as a full schema check: callers MUST run `check_incoming` (or `version_rejection`) first. `validate()` itself is unchanged and still raises `jsonschema.ValidationError` on an unknown version such as `"v0.3"`. (this release)
- `docs/TRANSPORT.md` now points the first-edition library boundary at the `ai_oriented_a2a` package; `examples/network_dual_end.py` remains the demo HTTP server. (this release)

### Added

- Installable stdlib package `ai_oriented_a2a` with `pyproject.toml` (hatchling). No required dependencies. Optional extra `[schema]` installs `jsonschema`. Entry point `check_incoming(msg, sign_key=None, replay_window=None, seen=None)` keeps signature and replay checks off unless arguments are passed. (#36)
- `examples/network_dual_end.py` imports core functions from the package and keeps the same HTTP demo behaviour. CI (`docs-and-examples`) installs the package before tests. (#36)

## [0.3.0] - 2026-11-13

Protocol v0.3.

### Breaking

- Absorption hard gate (SPEC §3.1): `status=absorbed` now MUST carry `effect_summary` with a non-empty `note`. An absorbed result without it is a protocol violation. Detailed metrics stay OPTIONAL in `effect_summary.metrics`. (#31)

### Added

- Optional on-wire `version` (`"0.3"`, no leading `v`), `nonce`, `timestamp`, `signature` on all message schemas; optional `identity` on Agent Profile. Old messages remain valid. (#32)
- Unknown versions (including `"v0.3"`) MUST be rejected with `unsupported_version`, not isolated.
- Legal error responses: signature failure, replay rejection, unknown version, and hard-gate failure return HTTP 200 and a valid Absorption Result; early failures without an id use the reserved `packet_ref` `"unknown"`.
- New docs: [docs/VERSIONING.md](docs/VERSIONING.md), [docs/TRANSPORT.md](docs/TRANSPORT.md).
- Reference `examples/network_dual_end.py`: HMAC verification and replay window are each opt-in (`--sign-key`, `--replay-window`); canonical JSON signing; 30s client timeout.

[0.4.1]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.3.3...v0.4.0
[0.3.3]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.2.0...v0.3.0
