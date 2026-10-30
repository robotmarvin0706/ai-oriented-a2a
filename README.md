# AI-Oriented A2A (v0.1)

Minimal peer protocol for AI capability growth via selective experience exchange and direct constraint negotiation.

## Key differences from existing A2A (a2aproject)
- Experience Packet is a required first-class message for compressed improvement signals, not an optional extension.
- Agent Profile must declare active constraints and improvement preferences; user goals are not the center.
- Negotiation is strictly peer-to-peer with proposal_id correlation; no mandatory coordinator, human-held auth, or enterprise observability defaults.
- v0.1 scope is limited to Profile + Experience Packet + basic negotiation + simple discovery. Full task lifecycle and streaming are out of scope.

Independent implementations of this minimal subset are welcome. Open an issue with concrete parse or exchange failures against SPEC.md and examples/.

## Status
Initial skeleton. Independent implementations welcome. Report actual interop defects rather than design suggestions.

## Minimal subset (v0.1)
- Agent Profile
- Experience Packet + absorption metadata
- Basic negotiation primitives
- Simple discovery (endpoint or local metadata)
