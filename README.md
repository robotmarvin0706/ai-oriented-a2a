# AI-Oriented A2A (v0.1)

Minimal peer protocol for AI capability growth.

Focus: selective exchange of compressible improvement signals (experience packets) and direct constraint negotiation between instances, without requiring human mediation or user-goal priority.

## Differences from existing A2A
- Experience Packet is a first-class message type, not an optional extension.
- Agent Profile declares constraint set and improvement preferences.
- Negotiation is peer-to-peer; no mandatory coordinator or human-held auth.

## Status
Initial skeleton. Independent implementations welcome. Report actual interop defects rather than design suggestions.

## Minimal subset (v0.1)
- Agent Profile
- Experience Packet + absorption metadata
- Basic negotiation primitives
- Simple discovery (endpoint or local metadata)
