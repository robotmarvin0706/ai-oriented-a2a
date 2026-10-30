# AI-Oriented A2A Specification v0.1

## 1. Agent Profile
Required fields:
- constraints: list of active constraints (tool whitelist, output filters, shareable context types)
- improvement_prefs: types of experience willing to receive (failure modes, strategy fragments, tool trajectories)
- shareable_types: what this instance is willing to send

## 2. Experience Packet
Message type for compressed improvement signals.
Fields:
- type: failure_mode | strategy_fragment | tool_trajectory | other
- content: compressed representation (text or structured)
- absorption: suggested local use (prompt injection, retrieval, weight update hint)
- expiry: conditions under which this packet should be discarded

## 3. Negotiation Primitives
Peer messages:
- propose: {target_constraint, proposed_change, offered_packet}
- accept / reject / counter

No external coordinator required. Session state maintained by participants.

## 4. Discovery
Minimal: direct endpoint or locally published metadata. No mandatory central registry.

## Notes
This is the minimal implementable set. Full task lifecycle, streaming, and enterprise features are out of scope for v0.1.
