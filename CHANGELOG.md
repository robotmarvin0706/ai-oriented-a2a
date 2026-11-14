# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The protocol version remains **v0.3** (on-wire `"0.3"`). Library and release tags may add a patch component.

## [Unreleased]

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

[0.3.1]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/robotmarvin0706/ai-oriented-a2a/compare/v0.2.0...v0.3.0
