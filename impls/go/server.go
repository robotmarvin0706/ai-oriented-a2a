package main

// HTTP binding (docs/TRANSPORT.md): POST /a2a/message, JSON in and out,
// 404 for other paths, 400 for unparseable bodies, 413 for a missing,
// invalid or too-large Content-Length (decided before the body is read),
// 500 for an internal failure. Every application-level failure is a valid
// Absorption Result.

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"
)

// maxBodyBytes is the announced-body limit (docs/TRANSPORT.md).
const maxBodyBytes = 1048576

// endpoint is the canonical path.
const endpoint = "/a2a/message"

// Server holds the receiver configuration and its shared state.
type Server struct {
	signKey         string
	replay          *ReplayCache
	guard           *NegotiationGuard
	coreConstraints []string
}

// NewServer builds a receiver. An empty signKey disables signature checks; a
// nil replayWindow disables the replay window.
func NewServer(signKey string, replayWindow *float64, coreConstraints []string) *Server {
	var cache *ReplayCache
	if replayWindow != nil {
		cache = NewReplayCache(*replayWindow)
	}
	return &Server{
		signKey:         signKey,
		replay:          cache,
		guard:           NewNegotiationGuard(defaultMaxNegotiationRounds),
		coreConstraints: append([]string(nil), coreConstraints...),
	}
}

// dispatch maps one parsed request to an HTTP status and a reply payload.
// closeAfter asks the connection loop to close after replying.
func (s *Server) dispatch(req *request) (status int, payload any, closeAfter bool) {
	if req.path != endpoint {
		return 404, reject(reservedUnknown, "not found: the only endpoint is "+endpoint), false
	}
	if req.method != "POST" {
		return 405, reject(reservedUnknown, "method not allowed: use POST"), false
	}
	length, ok := parseContentLength(req.header("Content-Length"))
	if !ok {
		return 413, reject(reservedUnknown, "payload_too_large: missing or invalid Content-Length"), true
	}
	if length > maxBodyBytes {
		return 413, reject(reservedUnknown,
			fmt.Sprintf("payload_too_large: request body exceeds %d bytes", maxBodyBytes)), true
	}
	parsed, err := decodeJSON(req.body)
	if err != nil {
		return 400, reject(reservedUnknown, "invalid json"), false
	}
	msg := asObject(parsed)
	if msg == nil {
		return 200, reject(reservedUnknown, "schema_invalid: message must be a JSON object"), false
	}
	reply, status := s.process(msg)
	return status, reply, false
}

// process runs the transport checks in the required order and then the
// message handler. Any panic becomes a 500 with a generic reason.
func (s *Server) process(msg map[string]any) (reply any, status int) {
	defer func() {
		if rec := recover(); rec != nil {
			reply = reject(packetRefOf(msg), "internal_error: message handler failed")
			status = 500
		}
	}()
	if res := checkVersion(msg); res != nil {
		return res, 200
	}
	if res := checkSignature(msg, s.signKey); res != nil {
		return res, 200
	}
	if res := s.replay.checkReplay(msg); res != nil {
		return res, 200
	}
	return s.handleMessage(msg), 200
}

// marshalJSON serialises a reply, signing it when a signing key is set.
func (s *Server) marshalJSON(reply any) ([]byte, error) {
	payload, err := toJSONMap(reply)
	if err != nil {
		return nil, err
	}
	if s.signKey != "" {
		if err := signMessage(payload, s.signKey); err != nil {
			return nil, err
		}
	}
	return json.Marshal(payload)
}

// marshalJSON serialises a reply without signing (used for transport-level
// errors that are not produced by the message handler).
func marshalJSON(reply any) ([]byte, error) {
	return json.Marshal(reply)
}

// toJSONMap converts a reply struct into a generic map so it can be signed.
func toJSONMap(reply any) (map[string]any, error) {
	raw, err := json.Marshal(reply)
	if err != nil {
		return nil, err
	}
	parsed, err := decodeJSON(raw)
	if err != nil {
		return nil, err
	}
	m := asObject(parsed)
	if m == nil {
		return nil, errors.New("reply is not a JSON object")
	}
	return m, nil
}

// parseContentLength accepts only a non-negative decimal integer.
func parseContentLength(raw string) (int64, bool) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return 0, false
	}
	for _, r := range raw {
		if r < '0' || r > '9' {
			return 0, false
		}
	}
	n, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || n < 0 {
		return 0, false
	}
	return n, true
}

// ListenAndServe binds 127.0.0.1:port and serves until the process exits.
func (s *Server) ListenAndServe(port int) error {
	addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(port))
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	return (&httpServer{server: s}).serve(ln)
}
