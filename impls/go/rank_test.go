package main

// Unit tests for the `rank` subcommand: the §7.2 history rule (exact rational
// comparison, deterministic tie-break, own-result attribution) and the §7.3
// claim rule (eligibility edge cases, value ordering, ineligible tail).

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// rankFixture decodes a JSON document into the generic form the rank code
// works on (json.Number for numbers).
func rankFixture(t *testing.T, raw string) any {
	t.Helper()
	v, err := decodeJSON([]byte(raw))
	if err != nil {
		t.Fatalf("decodeJSON: %v", err)
	}
	return v
}

// rankPackets decodes a JSON array of packets.
func rankPackets(t *testing.T, raw string) []map[string]any {
	t.Helper()
	v := rankFixture(t, raw)
	items, ok := v.([]any)
	if !ok {
		t.Fatalf("expected an array of packets")
	}
	packets, err := packetList(items)
	if err != nil {
		t.Fatalf("packetList: %v", err)
	}
	return packets
}

// rankOwn decodes a JSON array of own-result entries.
func rankOwn(t *testing.T, raw string) []any {
	t.Helper()
	v := rankFixture(t, raw)
	items, ok := v.([]any)
	if !ok {
		t.Fatalf("expected an array of own results")
	}
	return items
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func TestRankHistoryTieBreakByID(t *testing.T) {
	packets := rankPackets(t, `[
	  {"id": "pkt-c", "provenance": {"source_ref": "peer:1"}, "prior_outcomes": {"absorbed_count": 5, "rejected_count": 5}},
	  {"id": "pkt-a", "provenance": {"source_ref": "peer:2"}, "prior_outcomes": {"absorbed_count": 5, "rejected_count": 5}},
	  {"id": "pkt-b", "provenance": {"source_ref": "peer:3"}, "prior_outcomes": {"absorbed_count": 5, "rejected_count": 5}},
	  {"id": "pkt-d", "provenance": {"source_ref": "peer:4"}, "prior_outcomes": {"absorbed_count": 9, "rejected_count": 1}}
	]`)
	got := rankHistory(packets, nil)
	want := []string{"pkt-d", "pkt-a", "pkt-b", "pkt-c"}
	if !equalStrings(got, want) {
		t.Fatalf("rankHistory = %v, want %v", got, want)
	}
}

func TestRankHistoryExactRationalComparison(t *testing.T) {
	// 1/3 vs 2/6 are equal as rationals but differ as floats in general;
	// 2/6 must tie with 1/3 and lose to 3/7.
	packets := rankPackets(t, `[
	  {"id": "pkt-third", "prior_outcomes": {"absorbed_count": 1, "rejected_count": 2}},
	  {"id": "pkt-two-sixths", "prior_outcomes": {"absorbed_count": 2, "rejected_count": 4}},
	  {"id": "pkt-three-sevenths", "prior_outcomes": {"absorbed_count": 3, "rejected_count": 4}}
	]`)
	got := rankHistory(packets, nil)
	want := []string{"pkt-three-sevenths", "pkt-third", "pkt-two-sixths"}
	if !equalStrings(got, want) {
		t.Fatalf("rankHistory = %v, want %v", got, want)
	}
}

func TestRankHistoryMissingPriorOutcomes(t *testing.T) {
	packets := rankPackets(t, `[
	  {"id": "pkt-none"},
	  {"id": "pkt-some", "prior_outcomes": {"absorbed_count": 1, "rejected_count": 0}}
	]`)
	got := rankHistory(packets, nil)
	want := []string{"pkt-some", "pkt-none"}
	if !equalStrings(got, want) {
		t.Fatalf("rankHistory = %v, want %v", got, want)
	}
}

func TestRankHistoryOwnResultsAttribution(t *testing.T) {
	packets := rankPackets(t, `[
	  {"id": "pkt-a", "provenance": {"source_ref": "peer:a"}, "prior_outcomes": {"absorbed_count": 0, "rejected_count": 0}},
	  {"id": "pkt-b", "provenance": {"source_ref": "peer:b"}, "prior_outcomes": {"absorbed_count": 0, "rejected_count": 0}}
	]`)
	own := rankOwn(t, `[
	  {"result": {"packet_ref": "pkt-a", "status": "absorbed"}, "rolled_back": false},
	  {"result": {"packet_ref": "pkt-b", "status": "absorbed"}, "rolled_back": true},
	  {"result": {"packet_ref": "pkt-b", "status": "isolated"}, "rolled_back": false},
	  {"result": {"packet_ref": "pkt-missing", "status": "absorbed"}, "rolled_back": false}
	]`)
	got := rankHistory(packets, own)
	want := []string{"pkt-a", "pkt-b"}
	if !equalStrings(got, want) {
		t.Fatalf("rankHistory = %v, want %v", got, want)
	}
}

// A packet without a usable provenance.source_ref is not attributable to any
// source: own results pointing at it are attributed to nothing (not to an
// empty-string source), so its score uses prior_outcomes only.
func TestRankHistoryUnattributablePacketIgnoresOwnResults(t *testing.T) {
	packets := rankPackets(t, `[
	  {"id": "pkt-n1", "prior_outcomes": {"absorbed_count": 0, "rejected_count": 1}},
	  {"id": "pkt-n2", "provenance": {}, "prior_outcomes": {"absorbed_count": 0, "rejected_count": 1}},
	  {"id": "pkt-n3", "provenance": {"source_ref": ""}, "prior_outcomes": {"absorbed_count": 0, "rejected_count": 1}},
	  {"id": "pkt-n4", "provenance": {"source_ref": 7}, "prior_outcomes": {"absorbed_count": 0, "rejected_count": 1}},
	  {"id": "pkt-ok", "provenance": {"source_ref": "peer:ok"}, "prior_outcomes": {"absorbed_count": 0, "rejected_count": 1}}
	]`)
	own := rankOwn(t, `[
	  {"result": {"packet_ref": "pkt-n1", "status": "absorbed"}, "rolled_back": false},
	  {"result": {"packet_ref": "pkt-n2", "status": "absorbed"}, "rolled_back": false},
	  {"result": {"packet_ref": "pkt-n3", "status": "absorbed"}, "rolled_back": false},
	  {"result": {"packet_ref": "pkt-n4", "status": "absorbed"}, "rolled_back": false},
	  {"result": {"packet_ref": "pkt-ok", "status": "absorbed"}, "rolled_back": false}
	]`)
	counts := ownCountsBySource(packets, own)
	if _, ok := counts[""]; ok {
		t.Fatalf("own results were attributed to an empty-string source: %v", counts)
	}
	if len(counts) != 1 || counts["peer:ok"].Absorbed != 1 {
		t.Fatalf("own counts = %v, want only peer:ok with 1 absorbed", counts)
	}
	for _, p := range packets {
		a, r := historyScore(p, counts)
		if p["id"] == "pkt-ok" {
			if a != historyWeight || r != 1 {
				t.Fatalf("pkt-ok score = (%d,%d), want (%d,1)", a, r, historyWeight)
			}
			continue
		}
		if a != 0 || r != 1 {
			t.Fatalf("%v score = (%d,%d), want (0,1) (prior_outcomes only)", p["id"], a, r)
		}
	}
	got := rankHistory(packets, own)
	want := []string{"pkt-ok", "pkt-n1", "pkt-n2", "pkt-n3", "pkt-n4"}
	if !equalStrings(got, want) {
		t.Fatalf("rankHistory = %v, want %v", got, want)
	}
}

func TestRankClaimEligibility(t *testing.T) {
	packets := rankPackets(t, `[
	  {"id": "pkt-ok", "verification_claim": {"metric": "recovery_sr", "value": 0.5, "repro": {"n": 30}}},
	  {"id": "pkt-wrong-metric", "verification_claim": {"metric": "latency", "value": 0.9, "repro": {"n": 30}}},
	  {"id": "pkt-small-n", "verification_claim": {"metric": "recovery_sr", "value": 0.9, "repro": {"n": 29}}},
	  {"id": "pkt-float-n", "verification_claim": {"metric": "recovery_sr", "value": 0.9, "repro": {"n": 30.0}}},
	  {"id": "pkt-bool-n", "verification_claim": {"metric": "recovery_sr", "value": 0.9, "repro": {"n": true}}},
	  {"id": "pkt-no-value", "verification_claim": {"metric": "recovery_sr", "repro": {"n": 30}}},
	  {"id": "pkt-no-claim"},
	  {"id": "pkt-no-repro", "verification_claim": {"metric": "recovery_sr", "value": 0.9}}
	]`)
	for _, p := range packets {
		want := p["id"] == "pkt-ok"
		if got := claimEligible(p); got != want {
			t.Fatalf("claimEligible(%v) = %v, want %v", p["id"], got, want)
		}
	}
	got := rankClaim(packets)
	want := []string{
		"pkt-ok",
		"pkt-bool-n", "pkt-float-n", "pkt-no-claim", "pkt-no-repro",
		"pkt-no-value", "pkt-small-n", "pkt-wrong-metric",
	}
	if !equalStrings(got, want) {
		t.Fatalf("rankClaim = %v, want %v", got, want)
	}
}

func TestRankClaimValueOrderAndTies(t *testing.T) {
	packets := rankPackets(t, `[
	  {"id": "pkt-b", "verification_claim": {"metric": "recovery_sr", "value": 0.75, "repro": {"n": 40}}},
	  {"id": "pkt-a", "verification_claim": {"metric": "recovery_sr", "value": 0.75, "repro": {"n": 40}}},
	  {"id": "pkt-high", "verification_claim": {"metric": "recovery_sr", "value": 1, "repro": {"n": 100}}},
	  {"id": "pkt-low", "verification_claim": {"metric": "recovery_sr", "value": 0.1, "repro": {"n": 30}}}
	]`)
	got := rankClaim(packets)
	want := []string{"pkt-high", "pkt-a", "pkt-b", "pkt-low"}
	if !equalStrings(got, want) {
		t.Fatalf("rankClaim = %v, want %v", got, want)
	}
}

func TestRunRankCLI(t *testing.T) {
	dir := t.TempDir()
	poolPath := filepath.Join(dir, "pool.json")
	poolDoc := `{"pools": [
	  {"name": "p1", "packets": [
	    {"id": "pkt-x", "provenance": {"source_ref": "peer:x"}, "prior_outcomes": {"absorbed_count": 1, "rejected_count": 3}},
	    {"id": "pkt-y", "provenance": {"source_ref": "peer:y"}, "prior_outcomes": {"absorbed_count": 3, "rejected_count": 1}}
	  ], "own_results": []}
	]}`
	if err := os.WriteFile(poolPath, []byte(poolDoc), 0o644); err != nil {
		t.Fatalf("write pool: %v", err)
	}
	var stdout, stderr bytes.Buffer
	if code := runRank([]string{"--pool", poolPath, "--mode", "history", "--pool-name", "p1"}, &stdout, &stderr); code != 0 {
		t.Fatalf("runRank exit %d, stderr %q", code, stderr.String())
	}
	var doc struct {
		Mode    string   `json:"mode"`
		Ranking []string `json:"ranking"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &doc); err != nil {
		t.Fatalf("unmarshal %q: %v", stdout.String(), err)
	}
	if doc.Mode != "history" || !equalStrings(doc.Ranking, []string{"pkt-y", "pkt-x"}) {
		t.Fatalf("rank output = %+v", doc)
	}

	// Bad input: unknown mode and a missing pool name must exit non-zero.
	stdout.Reset()
	stderr.Reset()
	if code := runRank([]string{"--pool", poolPath, "--mode", "bogus"}, &stdout, &stderr); code == 0 {
		t.Fatalf("runRank accepted an unknown mode")
	}
	if !strings.Contains(stderr.String(), "mode") {
		t.Fatalf("stderr = %q, want a message about --mode", stderr.String())
	}
	// A file with several pools needs --pool-name; an unknown name is an error.
	multiPath := filepath.Join(dir, "multi.json")
	multiDoc := `{"pools": [
	  {"name": "p1", "packets": [{"id": "pkt-x"}]},
	  {"name": "p2", "packets": [{"id": "pkt-y"}]}
	]}`
	if err := os.WriteFile(multiPath, []byte(multiDoc), 0o644); err != nil {
		t.Fatalf("write multi pool: %v", err)
	}
	stdout.Reset()
	stderr.Reset()
	if code := runRank([]string{"--pool", multiPath, "--mode", "history"}, &stdout, &stderr); code == 0 {
		t.Fatalf("runRank accepted an ambiguous pool file without --pool-name")
	}
	stdout.Reset()
	stderr.Reset()
	if code := runRank([]string{"--pool", multiPath, "--mode", "history", "--pool-name", "nope"}, &stdout, &stderr); code == 0 {
		t.Fatalf("runRank accepted an unknown --pool-name")
	}
}
