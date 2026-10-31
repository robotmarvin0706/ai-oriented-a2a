# AI-Oriented A2A (v0.1)

Protocol for AI-to-AI capability growth: first-class Experience Packet exchange, constraint negotiation, proposal_id, no mandatory human mediation or central registry. See SPEC.md and examples/experience-packet.json.

Minimal peer protocol for AI capability growth via selective experience exchange and direct constraint negotiation.

## Key differences from existing A2A (a2aproject)

- Experience Packet is a required first-class message for compressed improvement signals, not an optional extension.
- Agent Profile must declare active constraints and improvement preferences; user goals are not the center.
- Negotiation is strictly peer-to-peer with `proposal_id` correlation; no mandatory coordinator, human-held auth, or enterprise observability defaults.
- v0.1 scope is limited to Profile + Experience Packet + basic negotiation + simple discovery. Full task lifecycle and streaming are out of scope.

## Contents

| Path | Role |
| --- | --- |
| [SPEC.md](SPEC.md) | Normative minimal subset |
| [schema/](schema/) | JSON Schemas for Profile, Experience Packet, Negotiation, Absorption Result |
| [examples/](examples/) | Concrete messages (Profile, packets, negotiation four types, absorption results) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to report defects and send PRs |
| [keywords.txt](keywords.txt) | Explicit term list for retrieval / indexing |

## Status

v0.1 minimal subset documented with schemas and examples. Independent implementations of this minimal subset are welcome. Report **actual** interop defects (parse or exchange failures against `SPEC.md`, `schema/`, and `examples/`) rather than open-ended design suggestions.

## Minimal subset (v0.1)

- Agent Profile
- Experience Packet + absorption metadata
- Basic negotiation primitives
- Simple discovery (endpoint or local metadata)
- Absorption Result (absorbed / rejected / isolated)

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
```
