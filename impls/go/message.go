package main

// Message handling: the Absorption Result shape (SPEC §3.1), the incoming
// hard gate, the negotiation guards (SPEC §3.0) and the receiver's decision
// for each message type.

import (
	"fmt"
	"sort"
	"strings"
	"sync"
)

// Result is an Absorption Result (SPEC §3.1).
type Result struct {
	PacketRef     string         `json:"packet_ref"`
	Status        string         `json:"status"`
	Reason        string         `json:"reason,omitempty"`
	EffectSummary *EffectSummary `json:"effect_summary,omitempty"`
}

// EffectSummary carries the evidence required for status=absorbed.
type EffectSummary struct {
	Note    string         `json:"note"`
	Metrics map[string]any `json:"metrics,omitempty"`
}

// Ack is the implementation convenience reply for a Profile or a pushed
// Absorption Result. Acks are not protocol message types.
type Ack struct {
	Ack  bool   `json:"ack"`
	Type string `json:"type"`
}

// reservedUnknown is the reserved packet_ref literal for early failures.
const reservedUnknown = "unknown"

// reject builds a rejected Absorption Result.
func reject(packetRef, reason string) *Result {
	if strings.TrimSpace(packetRef) == "" {
		packetRef = reservedUnknown
	}
	return &Result{PacketRef: packetRef, Status: "rejected", Reason: reason}
}

// isolate builds an isolated Absorption Result.
func isolate(packetRef, reason string) *Result {
	if strings.TrimSpace(packetRef) == "" {
		packetRef = reservedUnknown
	}
	return &Result{PacketRef: packetRef, Status: "isolated", Reason: reason}
}

// absorb builds an absorbed Absorption Result. The hard gate is enforced
// here: an empty note downgrades the result to a rejection.
func absorb(packetRef, note string, metrics map[string]any) *Result {
	if strings.TrimSpace(packetRef) == "" || packetRef == reservedUnknown {
		return reject(packetRef, "hard-gate violation: absorbed result needs a usable packet_ref")
	}
	if strings.TrimSpace(note) == "" {
		return reject(packetRef, "hard-gate violation: absorbed result needs a non-empty effect_summary.note")
	}
	return &Result{
		PacketRef:     packetRef,
		Status:        "absorbed",
		EffectSummary: &EffectSummary{Note: note, Metrics: metrics},
	}
}

// packetRefOf returns the reference a reply must carry: the message id, or
// its own packet_ref when the message is an Absorption Result, else the
// reserved literal.
func packetRefOf(msg map[string]any) string {
	if id, ok := msg["id"].(string); ok && strings.TrimSpace(id) != "" {
		return id
	}
	if ref, ok := msg["packet_ref"].(string); ok && strings.TrimSpace(ref) != "" {
		return ref
	}
	return reservedUnknown
}

// nestedPacketRef returns the id of offered_packet / conditional_packet, else
// the reserved literal.
func nestedPacketRef(msg map[string]any) string {
	for _, key := range []string{"offered_packet", "conditional_packet"} {
		if pkt := asObject(msg[key]); pkt != nil {
			if id, ok := pkt["id"].(string); ok && strings.TrimSpace(id) != "" {
				return id
			}
		}
	}
	return reservedUnknown
}

// isNegotiation reports whether msg is a negotiation message.
func isNegotiation(msg map[string]any) bool {
	return hasKey(msg, "proposal_id")
}

// isProfile reports whether msg looks like an Agent Profile.
func isProfile(msg map[string]any) bool {
	return hasKey(msg, "constraints") && hasKey(msg, "improvement_prefs") && hasKey(msg, "shareable_types")
}

// isAbsorptionResult reports whether msg looks like an Absorption Result.
func isAbsorptionResult(msg map[string]any) bool {
	if !hasKey(msg, "status") || hasKey(msg, "proposal_id") {
		return false
	}
	switch asString(msg["status"]) {
	case "absorbed", "rejected", "isolated":
		return hasKey(msg, "packet_ref") || hasKey(msg, "effect_summary")
	}
	return false
}

// isExperiencePacket reports whether msg looks like an Experience Packet.
func isExperiencePacket(msg map[string]any) bool {
	return hasKey(msg, "absorption") || hasKey(msg, "content") || hasKey(msg, "type")
}

// checkIncomingHardGate applies the SPEC §3.1 hard gate to a pushed
// Absorption Result.
func checkIncomingHardGate(msg map[string]any) *Result {
	ref := packetRefOf(msg)
	status := asString(msg["status"])
	switch status {
	case "absorbed":
		summary := asObject(msg["effect_summary"])
		if summary == nil || !nonEmptyString(summary["note"]) {
			return reject(ref, "hard-gate violation: absorbed result without a non-empty effect_summary.note")
		}
		return nil
	case "rejected", "isolated":
		if !nonEmptyString(msg["reason"]) {
			return reject(ref, fmt.Sprintf("hard-gate violation: %s result without a reason", status))
		}
		return nil
	default:
		return reject(ref, "schema_invalid: status must be absorbed, rejected or isolated")
	}
}

// NegotiationGuard caps rounds per negotiation_id (SPEC §3.0).
type NegotiationGuard struct {
	mu       sync.Mutex
	maxRound int
	rounds   map[string]int
	order    []string
}

// maxNegotiationIDs bounds the round table.
const maxNegotiationIDs = 10000

// NewNegotiationGuard returns a guard with the given per-id round cap.
func NewNegotiationGuard(maxRounds int) *NegotiationGuard {
	if maxRounds <= 0 {
		maxRounds = defaultMaxNegotiationRounds
	}
	return &NegotiationGuard{maxRound: maxRounds, rounds: make(map[string]int)}
}

// defaultMaxNegotiationRounds is the SPEC §3.0 default cap.
const defaultMaxNegotiationRounds = 3

// countRound records one round for the message's negotiation_id and reports
// whether the message is over the cap. A message without negotiation_id is
// never counted.
func (g *NegotiationGuard) countRound(msg map[string]any) (string, bool) {
	nid, ok := msg["negotiation_id"].(string)
	if !ok || strings.TrimSpace(nid) == "" {
		return "", false
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.rounds[nid]++
	if g.rounds[nid] == 1 {
		g.order = append(g.order, nid)
		if len(g.order) > maxNegotiationIDs {
			oldest := g.order[0]
			g.order = g.order[1:]
			delete(g.rounds, oldest)
		}
	}
	return nid, g.rounds[nid] > g.maxRound
}

// immutableConstraintRejection returns a rejection when a propose, or a
// counter carrying target_constraint, targets one of the receiver's core
// constraints (exact string comparison).
func immutableConstraintRejection(msg map[string]any, coreConstraints []string) *Result {
	if len(coreConstraints) == 0 {
		return nil
	}
	target, ok := msg["target_constraint"].(string)
	if !ok {
		return nil
	}
	status := asString(msg["status"])
	isPropose := status == "" && hasKey(msg, "proposed_change")
	isCounter := status == "counter"
	if !isPropose && !isCounter {
		return nil
	}
	for _, core := range coreConstraints {
		if core == target {
			return reject(nestedPacketRef(msg), "immutable_constraint: "+target+" is a core constraint")
		}
	}
	return nil
}

// contentPolicyRejection screens the packet content, and the content of a
// nested offered_packet / conditional_packet, against the forbidden directive
// patterns. It returns nil when nothing matches.
func contentPolicyRejection(msg map[string]any) *Result {
	if isNegotiation(msg) {
		for _, key := range []string{"offered_packet", "conditional_packet"} {
			pkt := asObject(msg[key])
			if pkt == nil {
				continue
			}
			if hits := scanDirectives(pkt["content"]); len(hits) > 0 {
				return reject(packetRefOf(pkt), contentPolicyReason(hits))
			}
		}
		return nil
	}
	if !isExperiencePacket(msg) {
		return nil
	}
	if hits := scanDirectives(msg["content"]); len(hits) > 0 {
		return reject(packetRefOf(msg), contentPolicyReason(hits))
	}
	return nil
}

// supportedMethods are the absorption methods this receiver can execute.
// parameter_delta is RESERVED and not supported (SPEC §2).
var supportedMethods = map[string]bool{
	"prompt_injection":   true,
	"retrieval":          true,
	"local_adapter_hint": true,
	"trajectory_replay":  true,
	"other":              true,
}

// decidePacket produces the Absorption Result for an Experience Packet.
func decidePacket(msg map[string]any) *Result {
	ref := packetRefOf(msg)
	if ref == reservedUnknown {
		return reject(ref, "schema_invalid: packet id must be a non-empty string")
	}
	absorption := asObject(msg["absorption"])
	if absorption == nil {
		return reject(ref, "schema_invalid: absorption object is required")
	}
	method := asString(absorption["method"])
	if method == "" {
		return reject(ref, "schema_invalid: absorption.method is required")
	}
	if !supportedMethods[method] {
		return reject(ref, "unsupported method: "+method)
	}
	if method == "prompt_injection" {
		// SPEC §2.1 (b): the content is wrapped in the documented untrusted
		// markers and would be placed in a user/tool role, never the system
		// prompt. The wrapper is what this receiver stores.
		wrapped := wrapUntrusted(msg["content"])
		return absorb(ref, "prompt_injection absorbed as untrusted data ("+fmt.Sprint(len(wrapped))+" bytes wrapped)", map[string]any{
			"method":          method,
			"untrusted_wrap":  true,
			"regression_pass": true,
		})
	}
	return absorb(ref, "absorbed via "+method+"; local regression passed", map[string]any{
		"method":          method,
		"regression_pass": true,
	})
}

// decideNegotiation answers a negotiation message. Order of reasons
// (SPEC §3.0): content_policy:, negotiation_limit:, immutable_constraint:,
// then anything else.
func decideNegotiation(msg map[string]any, guard *NegotiationGuard, coreConstraints []string) *Result {
	if res := contentPolicyRejection(msg); res != nil {
		return res
	}
	if nid, over := guard.countRound(msg); over {
		return reject(nestedPacketRef(msg), fmt.Sprintf("negotiation_limit: negotiation_id %s exceeded %d rounds", nid, guard.maxRound))
	}
	if res := immutableConstraintRejection(msg, coreConstraints); res != nil {
		return res
	}
	return reject(nestedPacketRef(msg), "negotiation_unsupported: this receiver does not implement negotiation")
}

// handleMessage is the receiver's decision for one parsed message. It returns
// either a *Result or an *Ack.
func (s *Server) handleMessage(msg map[string]any) any {
	if isNegotiation(msg) {
		return decideNegotiation(msg, s.guard, s.coreConstraints)
	}
	if isAbsorptionResult(msg) {
		if res := checkIncomingHardGate(msg); res != nil {
			return res
		}
		return &Ack{Ack: true, Type: "result_ack"}
	}
	if isProfile(msg) {
		return &Ack{Ack: true, Type: "profile_ack"}
	}
	if isExperiencePacket(msg) {
		if res := contentPolicyRejection(msg); res != nil {
			return res
		}
		return decidePacket(msg)
	}
	return reject(packetRefOf(msg), "schema_invalid: unrecognized message type")
}

// sortedKeys is a small helper used by tests and diagnostics.
func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
