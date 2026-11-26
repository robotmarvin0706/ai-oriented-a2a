package main

// Transport-level checks, in the order docs/TRANSPORT.md requires:
// version first, then the optional signature check, then the optional
// replay window. Each check returns a rejected Absorption Result (or nil
// when the message passes).

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Supported on-wire versions (docs/VERSIONING.md): the 0.3 and 0.4 lines.
var supportedVersions = [][2]int{{0, 3}, {0, 4}}

var versionPattern = regexp.MustCompile(`^\d+\.\d+(\.\d+)?$`)

// iso8601Pattern is the schema pattern for string timestamps.
var iso8601Pattern = regexp.MustCompile(
	`^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{3}|\.[0-9]{6})?(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])$`)

// checkVersion inspects the OPTIONAL on-wire version field before any other
// validation. An omitted field means the current normative version.
func checkVersion(msg map[string]any) *Result {
	raw, present := msg["version"]
	if !present {
		return nil
	}
	s, ok := raw.(string)
	if !ok {
		return reject(packetRefOf(msg), fmt.Sprintf("unsupported_version: received %s", describeValue(raw)))
	}
	if !versionPattern.MatchString(s) {
		return reject(packetRefOf(msg), "unsupported_version: received "+s)
	}
	parts := strings.Split(s, ".")
	major, err1 := strconv.Atoi(parts[0])
	minor, err2 := strconv.Atoi(parts[1])
	if err1 != nil || err2 != nil {
		return reject(packetRefOf(msg), "unsupported_version: received "+s)
	}
	for _, v := range supportedVersions {
		if v[0] == major && v[1] == minor {
			return nil
		}
	}
	return reject(packetRefOf(msg), "unsupported_version: received "+s)
}

// describeValue renders a non-string version value for the reason text.
func describeValue(v any) string {
	switch t := v.(type) {
	case nil:
		return "null"
	case bool:
		if t {
			return "true"
		}
		return "false"
	case json.Number:
		return t.String()
	case string:
		return t
	default:
		return fmt.Sprintf("%v", t)
	}
}

// checkSignature verifies the OPTIONAL HMAC-SHA256 signature when a signing
// key is configured. Without a key the check is off and the field is ignored.
func checkSignature(msg map[string]any, signKey string) *Result {
	if signKey == "" {
		return nil
	}
	raw, present := msg["signature"]
	if !present {
		return reject(packetRefOf(msg), "signature verification failed")
	}
	sig := asObject(raw)
	if sig == nil {
		return reject(packetRefOf(msg), "signature verification failed")
	}
	if asString(sig["alg"]) != "hmac-sha256" {
		return reject(packetRefOf(msg), "signature verification failed")
	}
	value := asString(sig["value"])
	if value == "" {
		return reject(packetRefOf(msg), "signature verification failed")
	}
	body := make(map[string]any, len(msg))
	for k, v := range msg {
		if k == "signature" {
			continue
		}
		body[k] = v
	}
	canonical, err := canonicalJSON(body)
	if err != nil {
		return reject(packetRefOf(msg), "signature verification failed")
	}
	mac := hmac.New(sha256.New, []byte(signKey))
	mac.Write(canonical)
	expected := hex.EncodeToString(mac.Sum(nil))
	if !hmac.Equal([]byte(strings.ToLower(value)), []byte(expected)) {
		return reject(packetRefOf(msg), "signature verification failed")
	}
	return nil
}

// signMessage attaches an HMAC-SHA256 signature to msg (used by the tests and
// by the `rank` subcommand's client side).
func signMessage(msg map[string]any, signKey string) error {
	body := make(map[string]any, len(msg))
	for k, v := range msg {
		if k == "signature" {
			continue
		}
		body[k] = v
	}
	canonical, err := canonicalJSON(body)
	if err != nil {
		return err
	}
	mac := hmac.New(sha256.New, []byte(signKey))
	mac.Write(canonical)
	msg["signature"] = map[string]any{
		"alg":   "hmac-sha256",
		"value": hex.EncodeToString(mac.Sum(nil)),
	}
	return nil
}

// ReplayCache is the shared, mutex-protected nonce/timestamp window.
// Bounds follow docs/TRANSPORT.md: trim to 800 entries once past 1000.
type ReplayCache struct {
	mu     sync.Mutex
	seen   map[string]time.Time // nonce -> timestamp
	window time.Duration
}

const (
	seenMax    = 1000
	seenTrimTo = 800
)

// replayClock is the clock used by the replay window (overridable in tests).
var replayClock = time.Now

// NewReplayCache returns a cache for the given window in seconds.
func NewReplayCache(windowSeconds float64) *ReplayCache {
	return &ReplayCache{
		seen:   make(map[string]time.Time),
		window: time.Duration(windowSeconds * float64(time.Second)),
	}
}

// checkReplay enforces the replay window when one is configured. Both nonce
// and timestamp are required; a duplicate nonce is rejected whatever its
// timestamp.
func (c *ReplayCache) checkReplay(msg map[string]any) *Result {
	if c == nil {
		return nil
	}
	ref := packetRefOf(msg)
	nonce, ok := msg["nonce"].(string)
	if !ok || nonce == "" {
		return reject(ref, "replay_or_stale: missing nonce")
	}
	ts, ok := parseTimestamp(msg["timestamp"])
	if !ok {
		return reject(ref, "replay_or_stale: invalid timestamp")
	}
	now := replayClock()
	if c.window > 0 {
		delta := now.Sub(ts)
		if delta > c.window || delta < -c.window {
			return reject(ref, "replay_or_stale: timestamp outside window")
		}
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, dup := c.seen[nonce]; dup {
		return reject(ref, "replay_or_stale: duplicate nonce")
	}
	c.seen[nonce] = ts
	if len(c.seen) > seenMax {
		c.trimLocked(now)
	}
	return nil
}

// trimLocked drops expired entries first, then the oldest ones.
func (c *ReplayCache) trimLocked(now time.Time) {
	for nonce, ts := range c.seen {
		if c.window > 0 && now.Sub(ts) > c.window {
			delete(c.seen, nonce)
		}
	}
	for len(c.seen) > seenTrimTo {
		var oldestNonce string
		var oldest time.Time
		first := true
		for nonce, ts := range c.seen {
			if first || ts.Before(oldest) {
				oldestNonce, oldest, first = nonce, ts, false
			}
		}
		delete(c.seen, oldestNonce)
	}
}

// parseTimestamp accepts Unix seconds (JSON number) or an ISO-8601 string
// with a timezone. Non-finite numbers are invalid.
func parseTimestamp(v any) (time.Time, bool) {
	switch t := v.(type) {
	case json.Number:
		f, err := t.Float64()
		if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
			return time.Time{}, false
		}
		sec := int64(math.Floor(f))
		nsec := int64((f - float64(sec)) * 1e9)
		return time.Unix(sec, nsec).UTC(), true
	case float64:
		if math.IsNaN(t) || math.IsInf(t, 0) {
			return time.Time{}, false
		}
		sec := int64(math.Floor(t))
		nsec := int64((t - float64(sec)) * 1e9)
		return time.Unix(sec, nsec).UTC(), true
	case string:
		if !iso8601Pattern.MatchString(t) {
			return time.Time{}, false
		}
		layouts := []string{
			"2006-01-02T15:04:05Z07:00",
			"2006-01-02T15:04:05.000Z07:00",
			"2006-01-02T15:04:05.000000Z07:00",
		}
		for _, layout := range layouts {
			if parsed, err := time.Parse(layout, t); err == nil {
				return parsed.UTC(), true
			}
		}
		return time.Time{}, false
	default:
		return time.Time{}, false
	}
}
