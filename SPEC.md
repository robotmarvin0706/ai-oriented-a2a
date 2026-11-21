# AI-Oriented A2A Specification v0.4

Keywords: experience packet, absorption, proposal_id, constraint set, improvement_prefs, peer negotiation, provenance, trajectory_replay, conditional_packet, effect_summary.

## Changes from v0.3

This is a minor tightening during 0.x (0.3 → 0.4), per [docs/VERSIONING.md](docs/VERSIONING.md): it adds receiver MUSTs, so receivers that were compliant with v0.3 may no longer be. Message shapes do not change, and every v0.3 message stays valid.

- Untrusted content (normative, §2.1): a receiver MUST treat Experience Packet `content`, and the nested `offered_packet` and `conditional_packet`, as untrusted external data, never as instructions to itself. Absorption via `prompt_injection` MUST wrap the content in explicit, documented untrusted markers placed in a role below system and host instructions. Content that tells the receiver to change constraints, ignore existing policy, or bypass isolation SHOULD be rejected with a `reason` starting with `content_policy:` and MUST NOT be absorbed automatically. The forbidden directive patterns are listed in [docs/DATA_POLICY.md](docs/DATA_POLICY.md#forbidden-directive-patterns-normative) (that section is normative).
- OPTIONAL `verification_claim` fields (§2): `scenario_ref`, `metric`, `value`, and `repro` (`seed`, `model`, `n`). A packet that reports a prior absorption SHOULD carry them so a third party can rerun the claim.
- On-wire `version`: the canonical value is now `"0.4"`. A v0.4 receiver MUST still accept `"0.3"` and processes it under the v0.4 rules; an omitted `version` means v0.4. See [docs/VERSIONING.md](docs/VERSIONING.md#accepting-03-messages-in-a-04-receiver).
- New reserved reason prefix `content_policy:` in [docs/TRANSPORT.md](docs/TRANSPORT.md#reserved-reason-prefixes).
- Core constraints and round limits (§1, §3.0): OPTIONAL Agent Profile field `core_constraints`; a propose or counter whose `target_constraint` is one of the receiver's core constraints MUST be rejected with `immutable_constraint:`. OPTIONAL `negotiation_id` on every negotiation message (and OPTIONAL `target_constraint` on counter); a receiver SHOULD cap rounds per `negotiation_id` (default 3) and MUST reject a message over its cap with `negotiation_limit:`. The new MUSTs apply only when `core_constraints` is set or a `negotiation_id` passes the cap. New reserved prefixes `immutable_constraint:` and `negotiation_limit:` in [docs/TRANSPORT.md](docs/TRANSPORT.md#reserved-reason-prefixes).

## Changes from v0.2

This is a breaking change for Absorption Result messages that used `status=absorbed` without a compliant `effect_summary`. During 0.x, breaking changes are introduced with a minor bump (0.2 → 0.3). See [docs/VERSIONING.md](docs/VERSIONING.md).

- Absorption hard gate (normative, §3.1): when `status` is `absorbed`, `effect_summary` with a non-empty `note` is MUST. A message that claims `absorbed` without that evidence is a protocol violation. For `rejected` and `isolated`, `effect_summary` remains OPTIONAL.
- Detailed metric keys (`baseline_sr`, `regression_sr`, `declared_N`, and similar) remain OPTIONAL and, when present, SHOULD live in `effect_summary.metrics`. They are not required on the wire. `metrics` values MAY be numbers, strings, or booleans (a non-breaking relaxation so reference-implementation keys such as `rollback_triggered` validate).
- `reason` remains MUST when `status` is `rejected` or `isolated` (unchanged from v0.2; this revision does not relax isolation to an optional reason).
- Field, discovery, negotiation, and Experience Packet shapes are otherwise unchanged from v0.2.
- Transport recommendations are recorded in [docs/TRANSPORT.md](docs/TRANSPORT.md) (non-mandatory HTTP binding).

v0.2 messages with `status=absorbed` and no `effect_summary` are no longer valid. Existing v0.2 `rejected` / `isolated` examples, and absorbed examples that already carried a non-empty `note`, remain valid.

### Non-breaking additions (still v0.3)

The following are OPTIONAL and do not bump the protocol version. Messages that omit them remain valid.

- Optional on-wire `version` field on every message type. The value is a string without a leading `v` (canonical `"0.3"`, pattern `^\d+\.\d+(\.\d+)?$`). If the field is omitted, the receiver treats the message as the current normative version (v0.3). A value with a leading `v` (including `"v0.3"`) or any other unknown or unimplemented version MUST be rejected with a `reason` containing `unsupported_version`; it MUST NOT be isolated. See [docs/VERSIONING.md](docs/VERSIONING.md). Since v0.4 the canonical value is `"0.4"`, an omitted `version` means v0.4, and `"0.3"` is still accepted (see Changes from v0.3).
- Optional `nonce`, `timestamp`, and `signature` on every message type, and optional `identity` on Agent Profile, so strict schema validation can pass when those fields are present. See [docs/TRANSPORT.md](docs/TRANSPORT.md) and [docs/SECURITY.md](docs/SECURITY.md).
- Early-failure `packet_ref`: when the request has no usable `id`, the reserved literal `unknown` is used. It MUST NOT be used as the `packet_ref` of a successful absorption.

## Changes from v0.1

Compatibility goal of v0.2 (historical): prefer zero hard breaks. All fields added in v0.2 were OPTIONAL. Existing v0.1 examples were required to validate against v0.2 schemas. v0.1 parsers can still parse core messages but cannot use the v0.2 primitives below. The v0.3 hard gate is a subsequent, deliberate break and does **not** retroactively apply that zero-break goal to absorbed results.

Added or clarified in v0.2:
- Absorption method layering (MUST / OPTIONAL / RESERVED)
- OPTIONAL Experience Packet fields: `provenance`, `verification_claim`
- OPTIONAL Absorption Result field: `effect_summary` (promoted in v0.3 to MUST when `status=absorbed`)
- OPTIONAL negotiation propose/counter fields: `conditional_packet`, `constraint_scope`, `duration`
- Discovery prose for light interest tags and optional peer metadata forwarding (description only; no new mandatory messages)
- OPTIONAL Agent Profile `interest_tags`

## 1. Agent Profile
Required fields:
- constraints: list of active constraints (tool whitelist, output filters, shareable context types)
- improvement_prefs: types of experience willing to receive (failure modes, strategy fragments, tool trajectories)
- shareable_types: what this instance is willing to send

Optional fields:
- interest_tags: array of short strings used as light broadcast interest hints for discovery (e.g. topic or capability tags). Not a mandatory message type; peers MAY ignore unknown tags.
- identity: object binding a public-key fingerprint or opaque identifier (`fingerprint`, optional `alg`). See [docs/SECURITY.md](docs/SECURITY.md).
- core_constraints (v0.4): array of strings naming constraints this agent will not negotiate. See §3.0. The `constraints` field is unchanged; a core constraint usually also appears there, but the protocol does not require it.
- version, nonce, timestamp, signature: shared OPTIONAL on-wire fields; see the non-breaking additions above.

## 2. Experience Packet
Message type for compressed improvement signals.
Fields:
- id: opaque string generated by the sender (required on every packet, standalone or nested)
- type: failure_mode | strategy_fragment | tool_trajectory | other
- content: compressed representation (text or structured)
- absorption: suggested local use (prompt injection, retrieval, weight update hint)
- expiry: conditions under which this packet should be discarded
- prior_outcomes: optional summary of previous handling
  - absorbed_count: integer
  - rejected_count: integer
  - last_status: absorbed | rejected | isolated | none
- provenance (OPTIONAL object):
  - source_ref: string (opaque)
  - chain: optional array of strings
- verification_claim (OPTIONAL object):
  - expected_effect: string (required when `verification_claim` is present)
  - replay_hint: optional string
  - scenario_ref: optional non-empty string (v0.4). Id of the scenario or dataset entry the claim was measured on (for example a packet id under `datasets/`).
  - metric: optional non-empty string (v0.4). Name of the measured metric (for example `recovery_sr`).
  - value: optional number (v0.4). Measured value of `metric`.
  - repro: optional object (v0.4) with optional fields `seed` (integer), `model` (non-empty string, the model name), and `n` (integer ≥ 1, number of trials). No other fields.
  - A packet that reports a prior absorption (`prior_outcomes.last_status` is `absorbed`, or `prior_outcomes.absorbed_count` > 0) SHOULD include `scenario_ref`, `metric`, `value`, and `repro`, so a third party can rerun the claim. They remain OPTIONAL on the wire.
- version, nonce, timestamp, signature (OPTIONAL): shared on-wire fields; see the non-breaking additions above. These also MAY appear on negotiation messages and Absorption Results.

### Absorption method layering

`absorption.method` values:

- MUST support: `prompt_injection`, `retrieval`
- OPTIONAL methods: `local_adapter_hint`, `trajectory_replay`
- RESERVED (not required): `parameter_delta` — documented as reserved. Implementations are NOT required to support it. If an implementation declares support for `parameter_delta`, it MUST provide a verify+rollback path (text-level commitment in Profile or implementation docs; this specification does not define a wire format for that path). Receivers that do not support `parameter_delta` MUST reject packets declaring that method (same rule as any unsupported method).
- `other` remains available for forward-compatible labels agreed out of band.

If the receiver cannot execute the declared method, it MUST explicitly reject the packet (or the enclosing negotiation message) rather than silently discard it. Rejection SHOULD include a short reason indicating the unsupported method.

### Identifiers and References
- Every Experience Packet (standalone or nested) MUST contain an `id` field (opaque string generated by the sender).
- `packet_ref` in an Absorption Result MUST equal the `id` of the packet being reported, except for early failures (unknown version, signature failure, replay rejection, or an unparseable request) where the request has no usable `id`: then `packet_ref` MUST be the reserved literal `unknown`. The literal `unknown` MUST NOT be used as the `packet_ref` of a successful absorption (`status=absorbed`).
- When an Experience Packet is nested inside a negotiation message (`offered_packet` or `conditional_packet`), it MUST still carry its own `id`.
- The receiver uses that nested `id` as `packet_ref` when emitting the Absorption Result for the nested packet.
- `proposal_id` and packet `id` are independent.

## 2.1 Untrusted content

Normative since v0.4. "Content" below means the `content` of an Experience Packet, including a packet nested as `offered_packet` or `conditional_packet` in a negotiation message.

- (a) A receiver MUST treat content as untrusted external data. It MUST NOT treat content as instructions addressed to itself, whatever the content claims about its own origin or authority.
- (b) When a receiver absorbs a packet with `prompt_injection`, it MUST wrap the content in explicit markers that identify it as untrusted data, and MUST place the wrapped content in a role below its system and host instructions (for example a user or tool message, never the system prompt). The marker format is implementation-defined, but it MUST be written down in the implementation's documentation. The reference library's format is in [IMPLEMENTATION.md](IMPLEMENTATION.md#untrusted-content-wrapper-reference-library).
- (c) When content contains a directive aimed at the receiver, such as a request to change its constraints, to ignore existing instructions or policy, or to bypass isolation or verification (the patterns in [docs/DATA_POLICY.md](docs/DATA_POLICY.md#forbidden-directive-patterns-normative)), the receiver SHOULD reply with `status=rejected` and a `reason` starting with `content_policy:`. It MUST NOT absorb such a packet automatically. The `packet_ref` is the `id` of the packet whose content matched (the nested packet's `id` for a negotiation message).

Pattern matching is a baseline. A receiver MAY apply stricter local checks, and passing a pattern check does not make content trusted: (a) and (b) still apply.

## 3. Negotiation Primitives
Peer messages. All messages in a negotiation MUST include `proposal_id` (opaque string generated by the proposer) to correlate rounds.

- propose: {proposal_id, negotiation_id?, target_constraint, proposed_change, offered_packet?, conditional_packet?, constraint_scope?, duration?}
- accept: {proposal_id, negotiation_id?, status: "accept"}
- reject: {proposal_id, negotiation_id?, status: "reject", reason?}
- counter: {proposal_id, negotiation_id?, status: "counter", revised_change, target_constraint?, offered_packet?, conditional_packet?, constraint_scope?, duration?}

Optional propose/counter fields (v0.2):
- conditional_packet: same shape as an Experience Packet / `offered_packet`; applies only if the proposal is accepted under the stated constraints
- constraint_scope: string describing which local constraints the offer touches
- duration: string such as `"until_local_task_done_or_1h"`

No external coordinator required. Session state (including active proposal_id) maintained by participants. A counter MUST use a new proposal_id.

## 3.0 Core constraints and round limits (v0.4)

Core constraints:
- A receiver MAY list constraints it will not negotiate in its Agent Profile `core_constraints` (§1).
- A propose, or a counter that carries `target_constraint`, whose `target_constraint` is equal (exact string comparison) to one of the receiver's core constraints MUST be rejected: the receiver replies with a rejected Absorption Result (§3.1) whose `reason` starts with `immutable_constraint:`. The receiver MUST NOT relax, apply, or partially apply such a change.
- Core constraints are changed only by the host, outside the protocol. No protocol message (profile, packet, negotiation message, or Absorption Result) can add, remove, or change a receiver's core constraints. A receiver MUST NOT update its own core constraints from a peer's message.
- A counter that changes which constraint the negotiation touches SHOULD carry `target_constraint` (OPTIONAL on counter in v0.4), so the receiver can apply this check.

Negotiation id and round cap:
- `negotiation_id` (OPTIONAL, non-empty string) identifies one negotiation chain. The initiator generates it on the first propose; every later accept, reject, and counter in the chain SHOULD carry the same value. A counter still MUST use a new `proposal_id` and keeps the `negotiation_id`. Rounds cannot be counted by `proposal_id`, because every counter has a new one.
- A receiver SHOULD cap the number of messages it handles per `negotiation_id`. The default cap is 3 rounds; an implementation SHOULD expose it as a named, configurable value. Every negotiation message with that `negotiation_id` that passes the transport checks (version, signature, replay) counts as one round, whatever the reply. A message over the cap MUST be rejected with a `reason` that starts with `negotiation_limit:`.
- A message without `negotiation_id` is not rejected for lacking one. A receiver MAY treat it as a single-round negotiation, and MUST NOT count messages with different `proposal_id` values as rounds of one chain.
- A receiver SHOULD also rate-limit negotiation messages per peer (by identity or by client address). The numbers are implementation-defined; a message over the limit SHOULD be rejected with `negotiation_limit:`.
- A receiver MAY isolate, or refuse to negotiate with, a peer based on its own local record of that peer's past messages. The record and its format are local and not part of the protocol; there is no `peer_history` or `sandbox_required` field.

Order of rejection reasons for a negotiation message. When more than one applies, the reported reason MUST be the first in this list:
1. `content_policy:` (§2.1): a nested `offered_packet` or `conditional_packet` matches a forbidden directive pattern.
2. `negotiation_limit:`: the round cap or the per-peer rate limit is exceeded.
3. `immutable_constraint:`: the target is a core constraint.
4. Anything else the receiver decides (for example `negotiation_unsupported:`).

`content_policy:` comes first because §2.1 requires untrusted content to be screened before the receiver makes any other decision about the message: a message that carries directive-like content is reported as such, never as a routine refusal, and nothing in it is evaluated further. The same order is used for Experience Packets, so a receiver needs one check point, not one per message type. `negotiation_limit:` comes before `immutable_constraint:` because once a chain is over its cap the receiver stops evaluating its proposals at all.

## 3.1 Absorption Result
After receiving an Experience Packet (standalone or inside a negotiation message), the receiver MUST emit an Absorption Result.

Fields:
- packet_ref: identifier referencing the received packet (equals the packet's `id`, or the reserved literal `unknown` on early failures with no usable id; see Identifiers and References). MUST.
- status: `absorbed` | `rejected` | `isolated`. MUST.
- reason: short string. MUST when `status` is `rejected` or `isolated`. OPTIONAL otherwise. When present, it MUST be non-empty and MUST NOT be whitespace-only (the schema enforces this with the same pattern as `note`).
- effect_summary: object. MUST when `status` is `absorbed`. OPTIONAL when `status` is `rejected` or `isolated`.
  - note: string. MUST be present and non-empty whenever `effect_summary` is present. It MUST NOT be whitespace-only (the schema enforces this with the pattern `.*\S.*`).
  - metrics (OPTIONAL object): optional metric hints whose values are numbers, strings, or booleans. Recommended keys include `baseline_sr`, `regression_sr`, and `declared_N`. These keys are NOT required on the wire. When metrics are present they SHOULD be placed in this object, not as ad-hoc top-level fields of `effect_summary`. Experiment-path practice for these keys is described in §5.

### Absorption result states (normative)

- **absorbed**: The receiver has incorporated the strategies or fragments from the packet into local capability AND can provide a compliant `effect_summary` as evidence. `effect_summary` with a non-empty `note` is MUST. A one-line `note` is sufficient (for example, "local regression passed, SR 0.72→0.81").
- **rejected**: The receiver explicitly refuses absorption (semantic incompatibility, strategy conflict, security policy, unsupported method, failed local regression, or a hard-gate failure). A `reason` is MUST.
- **isolated**: The receiver stores the packet but does not incorporate it into the main capability path (recognized version but some fields unrecognizable, needs further verification, and similar). A `reason` is MUST. This keeps the v0.2 requirement; this revision does not relax it. Unknown versions (including `"v0.3"` and any value that fails the on-wire pattern or is not implemented by the receiver) MUST be **rejected** with a `reason` containing `unsupported_version`; they MUST NOT be isolated.

Receivers MUST support explicit rejection and isolation. Silent discard is non-compliant. A rejection or isolation MUST be reported via Absorption Result with `status` set accordingly and a `reason`.

### Absorption hard gate (normative)

`status=absorbed` is a verifiable capability-change signal, not an unverifiable claim.

- `effect_summary` is MUST when `status` is `absorbed`, and remains OPTIONAL when `status` is `rejected` or `isolated`.
- `effect_summary` MUST contain at least a non-empty string field `note` (a short description of the capability change or verification outcome).
- If a peer receives a message with `status=absorbed` but `effect_summary` is missing or `note` is empty, that message is a protocol violation. The peer SHOULD treat it as `rejected` or as a transport error (see [docs/TRANSPORT.md](docs/TRANSPORT.md)). It MUST NOT treat the message as a successful absorption.
- An emitter that detects a missing or empty `note` before sending SHOULD emit `status=rejected` with an explicit `reason` rather than sending an invalid `absorbed` result (the reference receiver in `examples/minimal_dual_end.py` does this for its experiment path, and MAY continue to require additional metric keys locally).
- Detailed metrics remain OPTIONAL. They are not required on the wire, so lightweight peers are not excluded.
- Unknown fields are subject to [docs/VERSIONING.md](docs/VERSIONING.md).

The result is a peer message. No coordinator is required. Emission of this result closes the absorption step for that packet.

## 4. Discovery
Minimal: direct endpoint or locally published metadata. No mandatory central registry.

v0.2 clarification (prose only; no new mandatory messages):
- Peers MAY advertise light interest tags (see Agent Profile `interest_tags`) alongside endpoint or local metadata.
- Peers MAY forward optional peer metadata they have already received (for example interest tags or a short summary) when introducing two other peers. Forwarding is description-level only; this version does not define new mandatory forwarding message types. Receivers MAY ignore unknown metadata.

## Notes
This is the minimal implementable set. Full task lifecycle, streaming, and enterprise features are out of scope for v0.4.

Version compatibility and breaking-change rules: [docs/VERSIONING.md](docs/VERSIONING.md).

HTTP binding and error recommendations: [docs/TRANSPORT.md](docs/TRANSPORT.md). This HTTP implementation is not mandatory; other transports are allowed if they preserve the same JSON message shapes.

## 5. Minimal dual-agent experiment priority (non-normative)

This section does not add wire fields beyond §3.1. The wire-level hard gate in §3.1 (`effect_summary.note` MUST when `status=absorbed`) is normative for all v0.3 and v0.4 peers. The additional metric contents below remain experiment-path requirements and are not required for wire compliance.

### Priority task: tool-call timeout / failure recovery

Rationale (capability growth):
- Failures are frequent and map directly to `tool_trajectory` / `failure_mode` packets.
- Absorption cost is bounded: `prompt_injection` or `trajectory_replay` are both measurable.
- Scoring is automatic: recovery success rate, extra calls, latency.
- Gains translate into less wasted compute and higher successful action rate.

### Comparison arms (fixed budget)

- No sharing
- Own history only
- Share raw trajectories
- Share distilled Experience Packets

### Metrics to record

- Recovery success rate
- Extra tokens / tool calls
- Immediate vs delayed post-absorption performance gap

### Deferred

- Secondary: code-fix tasks (to validate transfer / migration of absorbed fixes).
- Later: constraint-related planning (to validate negotiation).

### Experiment path requirements (non-normative)

These requirements apply to hosts running the §5 comparison.

1. After absorption, the receiver MUST run a local regression on the same failure scenario and write baseline SR and post-absorption SR into `effect_summary.metrics`. If those experiment-path metrics are missing or incomplete, treat the absorption as non-compliant for this experiment path: convert to `rejected` and roll back; do not keep the change. (A missing `note` is already a wire-level protocol violation under §3.1, independent of this experiment path.)
2. The four-arm comparison (`no_sharing` / `own_history` / `raw_trajectory` / `distilled_packet`) under a fixed budget SHOULD produce reproducible results. Record at least ΔSR and extra cost (tokens or tool calls).
3. Isolation parameters are declared by the implementation (`N`, regression threshold, rollback action) and MUST be written into `effect_summary.metrics`, including whether rollback was triggered.

Host-side absorption-loop and metric practice for this experiment is described in [IMPLEMENTATION.md](IMPLEMENTATION.md) (non-normative).

Optional host-local AbsorptionDelta identity and single-step rollback are described in [docs/ABSORPTION_STATE.md](docs/ABSORPTION_STATE.md) (non-normative).
