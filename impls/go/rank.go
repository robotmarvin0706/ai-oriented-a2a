package main

// `rank` subcommand: the selection rules of docs/PREREG_SELECTION.md §7,
// applied to a frozen packet pool, with a DETERMINISTIC tie-breaker (packet id
// ascending) in place of the seeded RNG of the experiment.
//
// Input (all JSON, read from files):
//
//	pool file:  {"pools": [{"name": "...", "packets": [...], "own_results": [...]}, ...]}
//	            or a single pool object {"name": ..., "packets": [...], "own_results": [...]}
//	            or a bare array of packets [...]
//	own results file: [{"result": <Absorption Result>, "rolled_back": <bool>}, ...]
//	            or {"own_results": [...]}
//
// Output on stdout: {"mode": "history"|"claim", "ranking": ["<packet id>", ...]}
// — the full ranking of every packet in the pool, best first.
//
// §7.2 history: for a packet p whose provenance.source_ref is s,
//
//	a = prior_outcomes.absorbed_count + 3*own_absorbed(s)
//	r = prior_outcomes.rejected_count + 3*own_not_absorbed(s)
//	score = (a+1)/(a+r+2)
//
// Missing fields count as 0. Scores are compared exactly, as rationals:
// (a1+1)/(a1+r1+2) > (a2+1)/(a2+r2+2) iff (a1+1)*(a2+r2+2) > (a2+1)*(a1+r1+2).
// No float is involved, so the order cannot depend on rounding.
//
// §7.3 claim: eligible iff verification_claim.metric == "recovery_sr" and
// verification_claim.repro.n is an integer (not a bool, not a float) >= 30 and
// verification_claim.value is a number. Eligible packets come first, by value
// descending; then every ineligible packet, by packet id ascending.

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math/big"
	"os"
	"sort"
	"strings"
)

// historyWeight is the fixed weight of the receiver's own results (§7.2).
const historyWeight = 3

// claimMetric is the only metric a verification claim may report (§7.3).
const claimMetric = "recovery_sr"

// claimMinN is the smallest reproducible sample size a claim may report (§7.3).
const claimMinN = 30

// rankModes are the accepted --mode values.
var rankModes = []string{"history", "claim"}

// rankOptions is the parsed `rank` configuration.
type rankOptions struct {
	PoolPath   string
	Mode       string
	OwnResults string
	PoolName   string
}

// pool is one named packet pool of the fixture.
type pool struct {
	Name       string
	Packets    []map[string]any
	OwnResults []any
}

// ownCounts is the receiver's own tally for one source: absorbed and
// not-absorbed results (§7.2).
type ownCounts struct {
	Absorbed    int
	NotAbsorbed int
}

// scoredPacket is one packet with its history score as a rational a+1 over
// a+r+2.
type scoredPacket struct {
	ID string
	A  int64
	R  int64
}

// claimEntry is one packet with its claim value (eligible packets only).
type claimEntry struct {
	ID    string
	Value json.Number
}

// runRank implements the `rank` subcommand; it returns the process exit code.
func runRank(args []string, stdout, stderr io.Writer) int {
	opts, err := parseRankArgs(args)
	if err != nil {
		if errors.Is(err, flag.ErrHelp) {
			fmt.Fprint(stdout, usage)
			return 0
		}
		fmt.Fprintf(stderr, "a2a-go rank: %v\n", err)
		return 2
	}
	out, err := rankPool(opts)
	if err != nil {
		fmt.Fprintf(stderr, "a2a-go rank: %v\n", err)
		return 1
	}
	if _, err := fmt.Fprintf(stdout, "%s\n", out); err != nil {
		fmt.Fprintf(stderr, "a2a-go rank: %v\n", err)
		return 1
	}
	return 0
}

// parseRankArgs parses the `rank` flags.
func parseRankArgs(args []string) (*rankOptions, error) {
	fs := flag.NewFlagSet("rank", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	poolPath := fs.String("pool", "", "path to the packet pool JSON file (required)")
	mode := fs.String("mode", "", "selection rule: history or claim (required)")
	ownResults := fs.String("own-results", "", "path to a JSON file of own Absorption Results")
	poolName := fs.String("pool-name", "", "name of the pool to rank when the file holds several")
	if err := fs.Parse(args); err != nil {
		return nil, err
	}
	if fs.NArg() > 0 {
		return nil, fmt.Errorf("unexpected argument %q", fs.Arg(0))
	}
	if *poolPath == "" {
		return nil, errors.New("--pool is required")
	}
	if *mode == "" {
		return nil, fmt.Errorf("--mode is required (one of %s)", strings.Join(rankModes, ", "))
	}
	known := false
	for _, m := range rankModes {
		if *mode == m {
			known = true
		}
	}
	if !known {
		return nil, fmt.Errorf("--mode must be one of %s, got %q", strings.Join(rankModes, ", "), *mode)
	}
	return &rankOptions{
		PoolPath:   *poolPath,
		Mode:       *mode,
		OwnResults: *ownResults,
		PoolName:   *poolName,
	}, nil
}

// rankPool loads the pool, applies the selection rule and returns the JSON
// document for stdout.
func rankPool(opts *rankOptions) ([]byte, error) {
	raw, err := os.ReadFile(opts.PoolPath)
	if err != nil {
		return nil, fmt.Errorf("read pool: %w", err)
	}
	pools, err := parsePools(raw)
	if err != nil {
		return nil, fmt.Errorf("pool %s: %w", opts.PoolPath, err)
	}
	selected, err := selectPool(pools, opts.PoolName)
	if err != nil {
		return nil, err
	}
	own := selected.OwnResults
	if opts.OwnResults != "" {
		rawOwn, err := os.ReadFile(opts.OwnResults)
		if err != nil {
			return nil, fmt.Errorf("read own results: %w", err)
		}
		own, err = parseOwnResults(rawOwn)
		if err != nil {
			return nil, fmt.Errorf("own results %s: %w", opts.OwnResults, err)
		}
	}
	var ranking []string
	switch opts.Mode {
	case "history":
		ranking = rankHistory(selected.Packets, own)
	case "claim":
		ranking = rankClaim(selected.Packets)
	default:
		return nil, fmt.Errorf("unknown mode %q", opts.Mode)
	}
	doc := map[string]any{"mode": opts.Mode, "ranking": ranking}
	return json.Marshal(doc)
}

// parsePools accepts the fixture shapes described at the top of this file.
func parsePools(raw []byte) ([]pool, error) {
	v, err := decodeJSON(raw)
	if err != nil {
		return nil, err
	}
	switch t := v.(type) {
	case []any:
		packets, err := packetList(t)
		if err != nil {
			return nil, err
		}
		return []pool{{Name: "", Packets: packets}}, nil
	case map[string]any:
		if list, ok := t["pools"]; ok {
			items, ok := list.([]any)
			if !ok {
				return nil, errors.New(`"pools" must be an array`)
			}
			if len(items) == 0 {
				return nil, errors.New(`"pools" is empty`)
			}
			out := make([]pool, 0, len(items))
			for i, item := range items {
				obj, ok := item.(map[string]any)
				if !ok {
					return nil, fmt.Errorf("pools[%d] is not an object", i)
				}
				p, err := parsePoolObject(obj, i)
				if err != nil {
					return nil, err
				}
				out = append(out, p)
			}
			return out, nil
		}
		p, err := parsePoolObject(t, 0)
		if err != nil {
			return nil, err
		}
		return []pool{p}, nil
	default:
		return nil, errors.New("expected a pool object, a {\"pools\": [...]} object or an array of packets")
	}
}

// parsePoolObject reads one pool object.
func parsePoolObject(obj map[string]any, index int) (pool, error) {
	name := asString(obj["name"])
	packetsRaw, ok := obj["packets"]
	if !ok {
		return pool{}, fmt.Errorf("pool %d (%q) has no \"packets\"", index, name)
	}
	items, ok := packetsRaw.([]any)
	if !ok {
		return pool{}, fmt.Errorf("pool %q: \"packets\" must be an array", name)
	}
	packets, err := packetList(items)
	if err != nil {
		return pool{}, fmt.Errorf("pool %q: %w", name, err)
	}
	own := []any{}
	if ownRaw, ok := obj["own_results"]; ok && ownRaw != nil {
		list, ok := ownRaw.([]any)
		if !ok {
			return pool{}, fmt.Errorf("pool %q: \"own_results\" must be an array", name)
		}
		own = list
	}
	return pool{Name: name, Packets: packets, OwnResults: own}, nil
}

// packetList checks that every item is an object with a non-empty string id
// and that ids are unique.
func packetList(items []any) ([]map[string]any, error) {
	out := make([]map[string]any, 0, len(items))
	seen := make(map[string]bool, len(items))
	for i, item := range items {
		obj, ok := item.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("packets[%d] is not an object", i)
		}
		id, ok := obj["id"].(string)
		if !ok || id == "" {
			return nil, fmt.Errorf("packets[%d] has no non-empty string \"id\"", i)
		}
		if seen[id] {
			return nil, fmt.Errorf("duplicate packet id %q", id)
		}
		seen[id] = true
		out = append(out, obj)
	}
	return out, nil
}

// selectPool picks the pool named by --pool-name (or the only pool).
func selectPool(pools []pool, name string) (pool, error) {
	if name == "" {
		if len(pools) != 1 {
			names := make([]string, 0, len(pools))
			for _, p := range pools {
				names = append(names, p.Name)
			}
			return pool{}, fmt.Errorf("the pool file holds %d pools (%s); pass --pool-name",
				len(pools), strings.Join(names, ", "))
		}
		return pools[0], nil
	}
	for _, p := range pools {
		if p.Name == name {
			return p, nil
		}
	}
	names := make([]string, 0, len(pools))
	for _, p := range pools {
		names = append(names, p.Name)
	}
	return pool{}, fmt.Errorf("no pool named %q (have: %s)", name, strings.Join(names, ", "))
}

// parseOwnResults accepts a list of {"result": ..., "rolled_back": ...}
// entries, or an object with an "own_results" list.
func parseOwnResults(raw []byte) ([]any, error) {
	v, err := decodeJSON(raw)
	if err != nil {
		return nil, err
	}
	switch t := v.(type) {
	case []any:
		return t, nil
	case map[string]any:
		list, ok := t["own_results"]
		if !ok {
			return nil, errors.New(`expected an array of results or an object with "own_results"`)
		}
		items, ok := list.([]any)
		if !ok {
			return nil, errors.New(`"own_results" must be an array`)
		}
		return items, nil
	default:
		return nil, errors.New("expected an array of results or an object with \"own_results\"")
	}
}

// sourceOf returns provenance.source_ref, or "" when it is absent or not a
// string (§6.2: the source is the peer id).
func sourceOf(packet map[string]any) string {
	s, _ := sourceRef(packet)
	return s
}

// sourceRef returns provenance.source_ref and whether the packet is
// attributable to a source at all. A packet whose provenance is missing, not
// an object, or whose source_ref is missing, empty or not a string is NOT
// attributable: §7.2 counts own results "attributed to source s", and such a
// packet has no s. Its own counts are therefore 0, and own results that point
// at it are attributed to nothing (they must not be lumped into an
// empty-string source).
func sourceRef(packet map[string]any) (string, bool) {
	prov, ok := packet["provenance"].(map[string]any)
	if !ok {
		return "", false
	}
	s, ok := prov["source_ref"].(string)
	if !ok || s == "" {
		return "", false
	}
	return s, true
}

// priorCount returns a non-negative integer count from prior_outcomes, or 0
// when the field is missing or not a non-negative integer.
func priorCount(packet map[string]any, field string) int64 {
	prior, ok := packet["prior_outcomes"].(map[string]any)
	if !ok {
		return 0
	}
	n, ok := jsonInt(prior[field])
	if !ok || n < 0 {
		return 0
	}
	return n
}

// jsonInt reports whether v is a JSON integer (not a bool, not a float) and
// returns it.
func jsonInt(v any) (int64, bool) {
	num, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	n, err := num.Int64()
	if err != nil {
		return 0, false
	}
	return n, true
}

// jsonNumber reports whether v is a JSON number (not a bool) and returns it.
func jsonNumber(v any) (json.Number, bool) {
	num, ok := v.(json.Number)
	return num, ok
}

// ownCountsBySource attributes every own Absorption Result to the source of
// the pool packet it refers to (§7.2). Results whose packet_ref is not in the
// pool are ignored, and so are results whose packet is not attributable to a
// source (no non-empty string provenance.source_ref): they are attributed to
// nothing, not to an empty-string source.
func ownCountsBySource(packets []map[string]any, own []any) map[string]ownCounts {
	sourceByID := make(map[string]string, len(packets))
	for _, p := range packets {
		if s, ok := sourceRef(p); ok {
			sourceByID[asString(p["id"])] = s
		}
	}
	counts := make(map[string]ownCounts)
	for _, entry := range own {
		obj, ok := entry.(map[string]any)
		if !ok {
			continue
		}
		result, ok := obj["result"].(map[string]any)
		if !ok {
			continue
		}
		ref, ok := result["packet_ref"].(string)
		if !ok {
			continue
		}
		source, ok := sourceByID[ref]
		if !ok {
			continue
		}
		status := asString(result["status"])
		rolledBack, _ := obj["rolled_back"].(bool)
		c := counts[source]
		switch {
		case status == "absorbed" && !rolledBack:
			c.Absorbed++
		case status == "rejected" || status == "isolated" || (status == "absorbed" && rolledBack):
			c.NotAbsorbed++
		default:
			continue // unknown status: not an Absorption Result status
		}
		counts[source] = c
	}
	return counts
}

// historyScore returns a and r of §7.2 for one packet. A packet that is not
// attributable to a source gets no own counts (only prior_outcomes).
func historyScore(packet map[string]any, own map[string]ownCounts) (int64, int64) {
	var c ownCounts
	if s, ok := sourceRef(packet); ok {
		c = own[s]
	}
	a := priorCount(packet, "absorbed_count") + historyWeight*int64(c.Absorbed)
	r := priorCount(packet, "rejected_count") + historyWeight*int64(c.NotAbsorbed)
	return a, r
}

// compareScores compares (a1+1)/(a1+r1+2) with (a2+1)/(a2+r2+2) exactly.
// It returns -1, 0 or +1 as the first score is smaller, equal or larger.
func compareScores(a1, r1, a2, r2 int64) int {
	// Both denominators are positive: a, r >= 0.
	left := new(big.Int).Mul(big.NewInt(a1+1), big.NewInt(a2+r2+2))
	right := new(big.Int).Mul(big.NewInt(a2+1), big.NewInt(a1+r1+2))
	return left.Cmp(right)
}

// rankHistory returns the packet ids ordered by §7.2 score, descending, ties
// by packet id ascending.
func rankHistory(packets []map[string]any, own []any) []string {
	counts := ownCountsBySource(packets, own)
	scored := make([]scoredPacket, 0, len(packets))
	for _, p := range packets {
		a, r := historyScore(p, counts)
		scored = append(scored, scoredPacket{ID: asString(p["id"]), A: a, R: r})
	}
	sort.SliceStable(scored, func(i, j int) bool {
		if c := compareScores(scored[i].A, scored[i].R, scored[j].A, scored[j].R); c != 0 {
			return c > 0
		}
		return scored[i].ID < scored[j].ID
	})
	out := make([]string, len(scored))
	for i, s := range scored {
		out[i] = s.ID
	}
	return out
}

// claimEligible reports whether a packet's verification claim is eligible
// under §7.3.
func claimEligible(packet map[string]any) bool {
	claim, ok := packet["verification_claim"].(map[string]any)
	if !ok {
		return false
	}
	if asString(claim["metric"]) != claimMetric {
		return false
	}
	repro, ok := claim["repro"].(map[string]any)
	if !ok {
		return false
	}
	n, ok := jsonInt(repro["n"])
	if !ok || n < claimMinN {
		return false
	}
	_, ok = jsonNumber(claim["value"])
	return ok
}

// claimValue returns the claim value of an eligible packet.
func claimValue(packet map[string]any) json.Number {
	claim, _ := packet["verification_claim"].(map[string]any)
	num, _ := jsonNumber(claim["value"])
	return num
}

// compareNumbers compares two JSON numbers by exact value.
func compareNumbers(x, y json.Number) int {
	if xi, err := x.Int64(); err == nil {
		if yi, err := y.Int64(); err == nil {
			switch {
			case xi < yi:
				return -1
			case xi > yi:
				return 1
			default:
				return 0
			}
		}
	}
	xf, _, err := big.ParseFloat(x.String(), 10, 200, big.ToNearestEven)
	if err != nil {
		return strings.Compare(x.String(), y.String())
	}
	yf, _, err := big.ParseFloat(y.String(), 10, 200, big.ToNearestEven)
	if err != nil {
		return strings.Compare(x.String(), y.String())
	}
	return xf.Cmp(yf)
}

// rankClaim returns the packet ids ordered by §7.3: eligible by value
// descending (ties by id ascending), then all ineligible by id ascending.
func rankClaim(packets []map[string]any) []string {
	eligible := make([]claimEntry, 0, len(packets))
	ineligible := make([]string, 0, len(packets))
	for _, p := range packets {
		id := asString(p["id"])
		if claimEligible(p) {
			eligible = append(eligible, claimEntry{ID: id, Value: claimValue(p)})
			continue
		}
		ineligible = append(ineligible, id)
	}
	sort.SliceStable(eligible, func(i, j int) bool {
		if c := compareNumbers(eligible[i].Value, eligible[j].Value); c != 0 {
			return c > 0
		}
		return eligible[i].ID < eligible[j].ID
	})
	sort.Strings(ineligible)
	out := make([]string, 0, len(packets))
	for _, e := range eligible {
		out = append(out, e.ID)
	}
	out = append(out, ineligible...)
	return out
}
