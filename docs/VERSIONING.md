# A2A Experience Packet Versioning

## Goal
Protect the long-term value of already-absorbed packets and reduce rework caused by field drift.

## Current version

- The protocol version is the SPEC version: **v0.4**. The corresponding on-wire value is the string `0.4` (no leading `v`).
- Prior versions: v0.1, v0.2, v0.3.
- SPEC titles MAY continue to write `v0.4`. The on-wire value aligns with SemVer habit and has no leading `v`. Release tags MAY add a patch component (e.g. `v0.4.0`).

### Accepting 0.3 messages in a 0.4 receiver

v0.4 tightens receiver behaviour (SPEC §2.1, untrusted content) and adds OPTIONAL `verification_claim` fields. It does not change any message shape that v0.3 senders produce. So a v0.4 receiver:

- MUST accept on-wire `"0.3"` (and `"0.3.x"`) as a supported version, in addition to `"0.4"` (and `"0.4.x"`).
- Processes a `"0.3"` message under the v0.4 rules, including §2.1. There is no separate 0.3 processing mode.
- Treats a message without `version` as the current normative version (v0.4) and processes it under the v0.4 rules.

A v0.3 receiver will reject `"0.4"` with `unsupported_version`. A v0.4 sender that must reach v0.3 peers MAY omit `version`, or send `"0.3"` when the message uses no v0.4 field. Both in-repo implementations follow this rule (`SUPPORTED_MAJOR_MINOR` is `((0, 3), (0, 4))` in `ai_oriented_a2a` and in `second_impl`).

### Optional on-wire `version` field

Each message type (Agent Profile, Experience Packet, negotiation, Absorption Result) MAY carry an OPTIONAL top-level `version` field. Adding this field is a non-breaking change: messages that omit it remain valid.

- Type: string. Pattern: `^\d+\.\d+(\.\d+)?$`.
- Canonical current value: `"0.4"`. A patch component (`"0.4.0"`) MAY be sent and is treated as the same minor version (the 0.4 line). `"0.3"` / `"0.3.x"` are also accepted (see above).
- Values with a leading `v` (for example `"v0.4"` or `"v0.3"`) do not match the pattern. They are unknown versions.
- Any other value that fails the pattern is an unknown version.
- A value that matches the pattern but is not a version this receiver implements (for example `"0.5"` or `"0.2"`) is also an unknown version.
- On an unknown version the receiver MUST reject the entire message and return a `reason` containing `unsupported_version` (for example `unsupported_version: received 0.5` or `unsupported_version: received v0.4`). The receiver MUST NOT isolate the message, silently downgrade, or mark it as `absorbed`.
- This version check MUST happen before full schema validation. An unknown version, including `"v0.4"`, MUST still produce a valid Absorption Result (`status=rejected`, `reason` containing `unsupported_version`, HTTP 200 on the HTTP binding) rather than failing first as a schema-pattern error. See [TRANSPORT.md](TRANSPORT.md#version-check-before-schema-validation).
- If the field is omitted, the receiver MUST treat the message as the current normative version (v0.4) and continue processing.
- There is still no request-wrapper object such as `{ "version", "packet", ... }`; `version` sits on the message itself. See [TRANSPORT.md](TRANSPORT.md).

## Field rules
- **MUST**: Missing a MUST field makes the message illegal. The receiver MUST reject the entire packet (or the Absorption Result, if that is the illegal message) and return an explicit `reason`.
- **OPTIONAL**: Missing OPTIONAL fields are handled with default semantics.
- New fields default to OPTIONAL. A new field may be added only if it does not break existing MUST semantics.

### Unknown fields

The intended compatibility rule is:

- The receiver MUST ignore unrecognized OPTIONAL fields and continue processing known parts.
- If an unknown field would change the meaning of a MUST field, or the receiver cannot interpret a MUST field because of it, the receiver MUST reject the entire packet and return an explicit `reason`.

Caveat for published schemas: current JSON Schemas in `schema/` set `"additionalProperties": false`. The OPTIONAL `version`, `nonce`, `timestamp`, `signature`, and (on Agent Profile) `identity` fields are declared in those schemas so strict validation can pass. Other unrecognized fields are still schema-rejected until a later, non-breaking schema relaxation. Peers that do not schema-reject unknown OPTIONAL fields MUST still ignore them (MUST NOT treat unknown fields as absorbed-side effects).

## Version compatibility
- Versions use semantic versioning, written as `vMAJOR.MINOR` in the SPEC title (release tags MAY add a patch component, e.g. `v0.4.0`).
- During 0.x, a breaking change requires a **minor** bump (for example 0.2 → 0.3, 0.3 → 0.4), not a jump to 1.0. After 1.0, a breaking change requires a **major** bump.
- On an unrecognized version (when a version is declared, or when out-of-band agreement identifies a version the receiver does not implement):
  - The receiver MUST reject the entire packet and return a `reason` containing `unsupported_version`.
  - Silent downgrade, or partial absorption marked as `absorbed`, is forbidden.
- On a recognized version that contains unknown fields: follow the unknown-field rules above.

## Breaking-change criteria
The following are breaking changes:

- Removing or renaming a MUST field
- Changing the semantics or value range of a MUST field
- Promoting a previously OPTIONAL field to MUST
- Changing the absorption three-state decision rules (especially the absorbed hard gate in SPEC §3.1)
- Adding a receiver MUST that makes previously compliant receiver behaviour non-compliant (a specification tightening, for example SPEC §2.1 in v0.4)

The v0.2 → v0.3 bump is the introduction of that hard gate: `effect_summary.note` became MUST when `status=absorbed`. Subsequent changes to the three-state rules or to this hard gate are also breaking and require a further bump as above.

The v0.3 → v0.4 bump adds receiver MUSTs for untrusted packet content (SPEC §2.1): content is data, never instructions; `prompt_injection` absorption wraps it in documented untrusted markers below system and host instructions; directive-like content is not absorbed automatically. That is a tightening of what a compliant receiver may do, so it takes a minor bump under the 0.x rule. Message shapes are unchanged and the new `verification_claim` fields are OPTIONAL, which is why 0.4 receivers still accept `"0.3"`.

Non-breaking:

- Adding OPTIONAL fields
- Relaxing constraints on OPTIONAL fields
- Adding metric keys inside `effect_summary.metrics`
- Adding an OPTIONAL on-wire protocol-version field (now present; omitted messages stay valid)
- Adding OPTIONAL `nonce`, `timestamp`, `signature`, and (on Agent Profile) `identity` fields
- Relaxing `"additionalProperties": false` so unknown OPTIONAL fields can pass published schemas (this matches the intended rule above)

## Handling principles (SHOULD)
- Prefer zero hard breaks.
- Reject unknown versions with an explicit reason rather than ambiguous degradation.
- The local value of already-absorbed packets takes priority over “absorb as much as possible”.
