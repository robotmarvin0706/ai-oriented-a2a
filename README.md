# AI-Oriented A2A (v0.2)

Protocol for AI-to-AI capability growth: first-class Experience Packet exchange, constraint negotiation, proposal_id, no mandatory human mediation or central registry. See SPEC.md and examples/experience-packet.json.

Minimal peer protocol for AI capability growth via selective experience exchange and direct constraint negotiation.

## Changes from v0.1

- Absorption method layering: MUST (`prompt_injection`, `retrieval`), OPTIONAL (`local_adapter_hint`, `trajectory_replay`), RESERVED (`parameter_delta` with verify+rollback if declared).
- OPTIONAL Experience Packet fields: `provenance`, `verification_claim`.
- OPTIONAL Absorption Result field: `effect_summary` (not mandatory effect feedback).
- OPTIONAL negotiation propose/counter fields: `conditional_packet`, `constraint_scope`, `duration`.
- Discovery prose for light interest tags and optional peer metadata forwarding (no new mandatory messages).
- OPTIONAL Agent Profile `interest_tags`.
- Compatibility: prefer zero hard breaks; new fields are optional; all v0.1 examples still validate.

### v0.2 subset note

v0.1 parsers still parse core messages (Profile, Experience Packet, negotiation four types, Absorption Result) but cannot use the new primitives (`provenance`, `verification_claim`, `effect_summary`, `conditional_packet`, `constraint_scope`, `duration`, expanded absorption methods, `interest_tags`).

## Key differences from existing A2A (a2aproject)

- Experience Packet is a required first-class message for compressed improvement signals, not an optional extension.
- Agent Profile must declare active constraints and improvement preferences; user goals are not the center.
- Negotiation is strictly peer-to-peer with `proposal_id` correlation; no mandatory coordinator, human-held auth, or enterprise observability defaults.
- Scope is limited to Profile + Experience Packet + basic negotiation + simple discovery. Full task lifecycle and streaming are out of scope.

## Contents

| Path | Role |
| --- | --- |
| [SPEC.md](SPEC.md) | Normative minimal subset |
| [schema/](schema/) | JSON Schemas for Profile, Experience Packet, Negotiation, Absorption Result |
| [examples/](examples/) | Concrete messages (Profile, packets, negotiation, absorption results, v0.2 optionals) |
| [IMPLEMENTATION.md](IMPLEMENTATION.md) | Non-normative dual-endpoint implementation notes |
| [docs/ABSORPTION_STATE.md](docs/ABSORPTION_STATE.md) | Non-normative absorption delta identity and single-step rollback |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to report defects and send PRs |
| [keywords.txt](keywords.txt) | Explicit term list for retrieval / indexing |

## Status

v0.2 minimal subset documented with schemas and examples. Independent implementations of this minimal subset are welcome. Report **actual** interop defects (parse or exchange failures against `SPEC.md`, `schema/`, and `examples/`) rather than open-ended design suggestions. When reporting defects, specify the protocol version.

## Next steps

First dual-endpoint comparison task set (non-normative): tool-call timeout / failure recovery. See [SPEC.md §5](SPEC.md#5-minimal-dual-agent-experiment-priority-non-normative) and [IMPLEMENTATION.md](IMPLEMENTATION.md).

## Minimal subset (v0.2)

- Agent Profile (optional `interest_tags`)
- Experience Packet + absorption metadata (optional provenance / verification_claim; method layering)
- Basic negotiation primitives (optional conditional_packet / constraint_scope / duration)
- Simple discovery (endpoint or local metadata; light interest tags prose)
- Absorption Result (absorbed / rejected / isolated; optional effect_summary)

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
