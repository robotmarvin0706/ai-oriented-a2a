# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The protocol version remains **v0.3** (on-wire `"0.3"`). Library and release tags may add a patch component.

## [Unreleased]

### Added

- `docs/DATA_POLICY.md` (non-normative): redaction, `provenance.source_ref` prefixes (`synthetic:`, `local_run:`, `peer_absorbed:`), 3-hop chain limit with `content.chain_truncated`, required `expected_effect` + `replay_hint`, no numeric uplift in synthetic data, CC0 for corpora. SPEC and schemas are unchanged; `provenance` stays OPTIONAL on the wire.
- `datasets/timeout_recovery/`: 8 synthetic Experience Packets and 2 Absorption Results for the tool-call timeout / failure-recovery task, under CC0 1.0 (`datasets/LICENSE`).
- `tests/interop/test_datasets.py`: CI checks for `datasets/**` (schema, legal prefix, verification claim, chain length, no numeric uplift). The `docs-and-examples` workflow also requires `docs/DATA_POLICY.md` and `datasets/README.md`.

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

[0.3.1]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.2.0...v0.3.0
