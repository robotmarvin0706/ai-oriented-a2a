package main

// JSON helpers: a strict decoder that keeps numbers as json.Number, a
// canonical encoder (sorted keys, no whitespace, UTF-8) for HMAC signatures,
// and small accessors used by the message checks.

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"sort"
	"strconv"
	"strings"
)

// decodeJSON parses one JSON value from data. Numbers are kept as
// json.Number so that integer timestamps survive a round trip unchanged.
//
// Python's json module emits NaN, Infinity and -Infinity for non-finite
// floats, and docs/TRANSPORT.md requires such a timestamp to be answered with
// `replay_or_stale: invalid timestamp` rather than a parse error. encoding/json
// refuses those literals, so they are rewritten to the strings "NaN",
// "Infinity" and "-Infinity" before parsing; parseTimestamp rejects them.
func decodeJSON(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(rewriteNonFinite(data)))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	// Reject trailing garbage after the first value.
	if _, err := dec.Token(); err != io.EOF {
		return nil, errors.New("trailing data after JSON value")
	}
	return v, nil
}

// rewriteNonFinite replaces bare NaN / Infinity / -Infinity literals that
// appear outside JSON strings with quoted strings.
func rewriteNonFinite(data []byte) []byte {
	if !bytes.Contains(data, []byte("NaN")) &&
		!bytes.Contains(data, []byte("Infinity")) {
		return data
	}
	out := make([]byte, 0, len(data)+8)
	inString := false
	escaped := false
	for i := 0; i < len(data); i++ {
		c := data[i]
		if inString {
			out = append(out, c)
			switch {
			case escaped:
				escaped = false
			case c == '\\':
				escaped = true
			case c == '"':
				inString = false
			}
			continue
		}
		if c == '"' {
			inString = true
			out = append(out, c)
			continue
		}
		if c == 'N' && bytes.HasPrefix(data[i:], []byte("NaN")) {
			out = append(out, '"', 'N', 'a', 'N', '"')
			i += 2
			continue
		}
		if c == 'I' && bytes.HasPrefix(data[i:], []byte("Infinity")) {
			out = append(out, '"', 'I', 'n', 'f', 'i', 'n', 'i', 't', 'y', '"')
			i += 7
			continue
		}
		if c == '-' && bytes.HasPrefix(data[i:], []byte("-Infinity")) {
			out = append(out, '"', '-', 'I', 'n', 'f', 'i', 'n', 'i', 't', 'y', '"')
			i += 8
			continue
		}
		out = append(out, c)
	}
	return out
}

// canonicalJSON renders v the way the reference canonical form does:
// json.dumps(v, separators=(",", ":"), sort_keys=True, ensure_ascii=False).
func canonicalJSON(v any) ([]byte, error) {
	var buf bytes.Buffer
	if err := writeCanonical(&buf, v); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func writeCanonical(buf *bytes.Buffer, v any) error {
	switch t := v.(type) {
	case nil:
		buf.WriteString("null")
	case bool:
		if t {
			buf.WriteString("true")
		} else {
			buf.WriteString("false")
		}
	case string:
		buf.WriteString(encodeJSONString(t))
	case json.Number:
		buf.WriteString(canonicalNumber(t))
	case float64:
		buf.WriteString(canonicalNumber(json.Number(strconv.FormatFloat(t, 'g', -1, 64))))
	case int:
		buf.WriteString(strconv.Itoa(t))
	case int64:
		buf.WriteString(strconv.FormatInt(t, 10))
	case []any:
		buf.WriteByte('[')
		for i, item := range t {
			if i > 0 {
				buf.WriteByte(',')
			}
			if err := writeCanonical(buf, item); err != nil {
				return err
			}
		}
		buf.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		buf.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				buf.WriteByte(',')
			}
			buf.WriteString(encodeJSONString(k))
			buf.WriteByte(':')
			if err := writeCanonical(buf, t[k]); err != nil {
				return err
			}
		}
		buf.WriteByte('}')
	default:
		return fmt.Errorf("canonicalJSON: unsupported type %T", v)
	}
	return nil
}

// canonicalNumber normalises a JSON number the way Python's json module
// prints it: integers without a fraction, floats in shortest round-trip form.
func canonicalNumber(n json.Number) string {
	s := n.String()
	if !strings.ContainsAny(s, ".eE") {
		return s
	}
	f, err := n.Float64()
	if err != nil {
		return s
	}
	if f == math.Trunc(f) && math.Abs(f) < 1e16 {
		return strconv.FormatFloat(f, 'f', -1, 64)
	}
	return strconv.FormatFloat(f, 'g', -1, 64)
}

// encodeJSONString writes a JSON string literal with UTF-8 preserved
// (ensure_ascii=False) and the same escapes Python's json module uses.
func encodeJSONString(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if r < 0x20 {
				b.WriteString(fmt.Sprintf(`\u%04x`, r))
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

// asObject returns v as a JSON object, or nil.
func asObject(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

// asString returns v as a string, or "" when it is not a string.
func asString(v any) string {
	s, _ := v.(string)
	return s
}

// nonEmptyString reports whether v is a string with at least one
// non-whitespace character.
func nonEmptyString(v any) bool {
	s, ok := v.(string)
	return ok && strings.TrimSpace(s) != ""
}

// hasKey reports whether m contains key.
func hasKey(m map[string]any, key string) bool {
	_, ok := m[key]
	return ok
}
