# AI-Oriented A2A (v0.3)

Minimal **peer** protocol for AI capability growth: Experience Packets + constraint negotiation.
Not the Google A2A task/agent-card stack — this repo is about sharing compressed improvement signals between peers.
- Spec: [SPEC.md](SPEC.md) (v0.3) · Last tagged release: [v0.3.1](https://github.com/robotmarvin0706/ai-oriented-a2a/releases/tag/v0.3.1) · Changelog: [CHANGELOG.md](CHANGELOG.md)
- Library version on `main`: 0.3.2 (protocol v0.3). Tag `v0.3.2` is created after this version is merged, so v0.3.1 stays the last tagged release until then.
- Keywords: experience packet, absorption, proposal_id, peer negotiation, trajectory_replay

Protocol for AI-to-AI capability growth: first-class Experience Packet exchange, constraint negotiation, proposal_id, no mandatory human mediation or central registry. See SPEC.md and examples/experience-packet.json.

Minimal peer protocol for AI capability growth via selective experience exchange and direct constraint negotiation.

## Changes from v0.1

- Absorption method layering: MUST (`prompt_injection`, `retrieval`), OPTIONAL (`local_adapter_hint`, `trajectory_replay`), RESERVED (`parameter_delta` with verify+rollback if declared).
- OPTIONAL Experience Packet fields: `provenance`, `verification_claim`.
- OPTIONAL Absorption Result field in v0.2: `effect_summary`. In v0.3 this is MUST when `status=absorbed` (non-empty `note`); it remains OPTIONAL for `rejected` and `isolated`.
- OPTIONAL negotiation propose/counter fields: `conditional_packet`, `constraint_scope`, `duration`.
- Discovery prose for light interest tags and optional peer metadata forwarding (no new mandatory messages).
- OPTIONAL Agent Profile `interest_tags`.
- Compatibility: v0.2 preferred zero hard breaks and kept new fields optional. v0.3 is a deliberate break: `status=absorbed` without `effect_summary.note` is no longer valid. See [docs/VERSIONING.md](docs/VERSIONING.md).

### v0.2 subset note

v0.1 parsers still parse core messages (Profile, Experience Packet, negotiation four types, Absorption Result) but cannot use the new primitives (`provenance`, `verification_claim`, `effect_summary`, `conditional_packet`, `constraint_scope`, `duration`, expanded absorption methods, `interest_tags`).

### v0.3 subset note

v0.3 does not add message types. It tightens Absorption Result: a peer that emits `status=absorbed` MUST include `effect_summary` with a non-empty `note`. v0.2 absorbed results that omitted `effect_summary` are not valid under v0.3 schemas. Metric keys stay OPTIONAL.

Non-breaking additions still under v0.3 (all OPTIONAL; omitted messages remain valid): an on-wire `version` field (canonical `"0.3"`, no leading `v`); `nonce`, `timestamp`, and `signature` on every message type; and `identity` on Agent Profile. See [docs/VERSIONING.md](docs/VERSIONING.md) and [docs/TRANSPORT.md](docs/TRANSPORT.md).

## Key differences from existing A2A (a2aproject)

- Experience Packet is a required first-class message for compressed improvement signals, not an optional extension.
- Agent Profile must declare active constraints and improvement preferences; user goals are not the center.
- Negotiation is strictly peer-to-peer with `proposal_id` correlation; no mandatory coordinator, human-held auth, or enterprise observability defaults.
- Scope is limited to Profile + Experience Packet + basic negotiation + simple discovery. Full task lifecycle and streaming are out of scope.

## Contents

| Path | Role |
| --- | --- |
| [SPEC.md](SPEC.md) | Normative minimal subset (v0.3) |
| [schema/](schema/) | JSON Schemas for Profile, Experience Packet, Negotiation, Absorption Result |
| [examples/](examples/) | Concrete messages (Profile, packets, negotiation, absorption results) |
| [IMPLEMENTATION.md](IMPLEMENTATION.md) | Non-normative dual-endpoint implementation notes |
| [docs/VERSIONING.md](docs/VERSIONING.md) | Field rules, unknown versions, breaking-change criteria |
| [docs/TRANSPORT.md](docs/TRANSPORT.md) | HTTP binding, error recommendations, retries |
| [docs/SECURITY.md](docs/SECURITY.md) | Non-normative signature and replay recommendations |
| [docs/ABSORPTION_STATE.md](docs/ABSORPTION_STATE.md) | Non-normative absorption delta identity and single-step rollback |
| [docs/DATA_POLICY.md](docs/DATA_POLICY.md) | Non-normative trust and safety rules for public Experience Packet corpora |
| [datasets/](datasets/) | Public synthetic Experience Packet corpora (CC0), checked in CI |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to report defects and send PRs |
| [keywords.txt](keywords.txt) | Explicit term list for retrieval / indexing |
| [ai_oriented_a2a/](ai_oriented_a2a/) | Minimal installable stdlib helpers (canonical JSON, optional HMAC / replay / version checks) |
| [ai_oriented_a2a/transport.py](ai_oriented_a2a/transport.py) | Stdlib HTTP binding: `send_message` client and `POST /a2a/message` request handler / server (import `ai_oriented_a2a.transport` explicitly) |
| [pyproject.toml](pyproject.toml) | Packaging metadata: `pip install ai-oriented-a2a` or `pip install ".[schema]"` from a checkout |
| [docs/RELEASING.md](docs/RELEASING.md) | Release checklist and PyPI publishing steps (maintainers) |
| [second_impl/](second_impl/) | Independent second implementation (client, receiver, CLI); does not import `ai_oriented_a2a` |
| [mcp_a2a/](mcp_a2a/) | One-way MCP bridge: MCP hosts send packets and report results to A2A peers (optional `[mcp]` extra, Python ≥ 3.10) |
| [docs/COMPLIANCE.md](docs/COMPLIANCE.md) | What an implementation must pass, and how to run the conformance suite against another implementation |
| [docs/KNOWN_GAPS.md](docs/KNOWN_GAPS.md) | Known deviations in the reference implementation and test debts, with fix plans |

## Status

v0.3 minimal subset documented with schemas and examples. Independent implementations of this minimal subset are welcome. Report **actual** interop defects (parse or exchange failures against `SPEC.md`, `schema/`, and `examples/`) rather than open-ended design suggestions. When reporting defects, specify the protocol version.

## Next steps

First dual-endpoint comparison task set (non-normative): tool-call timeout / failure recovery. See [SPEC.md §5](SPEC.md#5-minimal-dual-agent-experiment-priority-non-normative) and [IMPLEMENTATION.md](IMPLEMENTATION.md).

## Minimal subset (v0.3)

- Agent Profile (optional `interest_tags`)
- Experience Packet + absorption metadata (optional provenance / verification_claim; method layering)
- Basic negotiation primitives (optional conditional_packet / constraint_scope / duration)
- Simple discovery (endpoint or local metadata; light interest tags prose)
- Absorption Result (absorbed / rejected / isolated; `effect_summary.note` MUST when absorbed)
- Optional on-wire `version`, `nonce`, `timestamp`, `signature`; optional Agent Profile `identity`

## Install

The core library uses only the Python standard library. Schema validation is an optional extra. Signature verification and the replay window stay off unless you pass the corresponding arguments.

```bash
pip install ai-oriented-a2a
# optional JSON Schema validation:
pip install "ai-oriented-a2a[schema]"
```

The PyPI commands work once `ai-oriented-a2a` has been published to PyPI. The import name is `ai_oriented_a2a`.

For development, install from a checkout or straight from GitHub:

```bash
pip install -e ".[schema]"
# or, without cloning:
pip install "ai-oriented-a2a[schema] @ git+https://github.com/robotmarvin0706/ai-oriented-a2a"
```

Maintainers: see [docs/RELEASING.md](https://github.com/robotmarvin0706/ai-oriented-a2a/blob/main/docs/RELEASING.md) for the release and PyPI publishing steps.

```python
from ai_oriented_a2a import check_incoming, sign_message

msg = {"id": "pkt-1", "version": "0.3"}
# Default: version check only; signature and replay stay off.
rejected = check_incoming(msg)  # None, or a legal rejected Absorption Result

# Opt in by passing arguments. Reuse `seen` across calls when the window is on.
seen = set()
signed = sign_message(msg, "secret")
rejected = check_incoming(signed, sign_key="secret", replay_window=600, seen=seen)
```

The HTTP binding (`POST /a2a/message`) ships as a submodule. It is not re-exported, so `import ai_oriented_a2a` does not load `http.server`; import it explicitly:

```python
from ai_oriented_a2a.transport import send_message, make_server, serve

def on_message(msg):
    # Called only after the version / signature / replay checks pass.
    # The reply passes the absorption hard gate before it is sent.
    return {"packet_ref": msg["id"], "status": "isolated", "reason": "isolated: no local regression"}

server = make_server(on_message, port=8002, sign_key="secret", replay_window=600)
# server.serve_forever()  (or serve(on_message, 8002, ...) to block)

reply = send_message("http://127.0.0.1:8002", msg, sign_key="secret", add_replay_fields=True)
```

`send_message` options are keyword-only (`timeout` defaults to 30 seconds). If the callback raises, the peer gets HTTP 500 with a rejected Absorption Result whose reason starts with `internal_error:`. See [docs/TRANSPORT.md](https://github.com/robotmarvin0706/ai-oriented-a2a/blob/main/docs/TRANSPORT.md#first-edition-minimal-library-boundary).

String `timestamp` values must be ISO-8601 with a timezone (for example `2026-11-14T08:00:00Z`); integer Unix seconds are recommended.

`examples/network_dual_end.py` remains the HTTP dual-endpoint demo and is built on `ai_oriented_a2a.transport`. New code should use the package module rather than copy the demo. It still runs without installing the package:

```bash
python3 examples/network_dual_end.py --role receiver --port 8002
```

## Quick local check

```bash
python3 -c "import json; json.load(open('examples/experience-packet.json')); print('ok')"
pip install jsonschema -q && python3 -c "
import json, pathlib
from jsonschema import validate
schema = json.load(open('schema/experience-packet.schema.json'))
instance = json.load(open('examples/experience-packet.json'))
validate(instance, schema)
print('schema ok')
"
```

Non-normative one-round demo (not a reference implementation):

```bash
python3 examples/demo_round.py
```

## Keywords

```
experience packet
absorption
proposal_id
constraint set
improvement_prefs
peer negotiation
AI-to-AI
no mandatory human mediation
no central registry
provenance
trajectory_replay
conditional_packet
```
