# A2A Experience Packet Versioning

## Goal
Protect the long-term value of already-absorbed packets and reduce rework caused by field drift.

## Current version

- The protocol version is the SPEC version: **v0.3**.
- Prior versions: v0.1, v0.2.
- Message types (Agent Profile, Experience Packet, negotiation, Absorption Result) do not yet carry an on-wire `version` field. Version agreement is out-of-band: the SPEC / release a peer implements. A future OPTIONAL on-wire version field may be added without breaking MUST semantics (see non-breaking changes below).
- The HTTP binding in [TRANSPORT.md](TRANSPORT.md) likewise has no request-wrapper `version` field today.

## Field rules
- **MUST**: Missing a MUST field makes the message illegal. The receiver MUST reject the entire packet (or the Absorption Result, if that is the illegal message) and return an explicit `reason`.
- **OPTIONAL**: Missing OPTIONAL fields are handled with default semantics.
- New fields default to OPTIONAL. A new field may be added only if it does not break existing MUST semantics.

### Unknown fields

The intended compatibility rule is:

- The receiver MUST ignore unrecognized OPTIONAL fields and continue processing known parts.
- If an unknown field would change the meaning of a MUST field, or the receiver cannot interpret a MUST field because of it, the receiver MUST reject the entire packet and return an explicit `reason`.

Caveat for published schemas: current JSON Schemas in `schema/` set `"additionalProperties": false`. Implementations that validate strictly against those schemas will therefore reject unknown fields until a later, non-breaking schema relaxation. Peers that do not schema-reject unknown OPTIONAL fields MUST still ignore them (MUST NOT treat unknown fields as absorbed-side effects). This document does not change those schemas in v0.3.

## Version compatibility
- Versions use semantic versioning, written as `vMAJOR.MINOR` in the SPEC title (release tags MAY add a patch component, e.g. `v0.3.0`).
- During 0.x, a breaking change requires a **minor** bump (for example 0.2 → 0.3), not a jump to 1.0. After 1.0, a breaking change requires a **major** bump.
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

The v0.2 → v0.3 bump is the introduction of that hard gate: `effect_summary.note` became MUST when `status=absorbed`. Subsequent changes to the three-state rules or to this hard gate are also breaking and require a further bump as above.

Non-breaking:

- Adding OPTIONAL fields
- Relaxing constraints on OPTIONAL fields
- Adding metric keys inside `effect_summary.metrics`
- Adding an OPTIONAL on-wire protocol-version field
- Relaxing `"additionalProperties": false` so unknown OPTIONAL fields can pass published schemas (this matches the intended rule above)

## Handling principles (SHOULD)
- Prefer zero hard breaks.
- Reject unknown versions with an explicit reason rather than ambiguous degradation.
- The local value of already-absorbed packets takes priority over “absorb as much as possible”.
