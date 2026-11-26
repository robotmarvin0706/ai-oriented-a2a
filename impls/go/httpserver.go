package main

// A minimal HTTP/1.1 server for the A2A binding.
//
// net/http rejects a request whose Content-Length header is not a plain
// decimal integer before any handler runs, and answers with its own plain-text
// 400. docs/TRANSPORT.md requires HTTP 413 and a valid Absorption Result for a
// missing or invalid Content-Length, decided from the header before the body
// is read, so this file parses the request line and headers itself and only
// then hands the body to the message handler.

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"time"
)

const (
	// maxHeaderBytes bounds the request line plus headers.
	maxHeaderBytes = 64 * 1024
	// readHeaderTimeout bounds how long a client may take to send headers.
	readHeaderTimeout = 30 * time.Second
	// idleTimeout bounds a keep-alive connection waiting for the next request.
	idleTimeout = 60 * time.Second
)

// request is a parsed HTTP request.
type request struct {
	method  string
	path    string
	proto   string
	headers map[string]string
	body    []byte
}

// header returns a header value (case-insensitive), or "".
func (r *request) header(name string) string {
	return r.headers[strings.ToLower(name)]
}

// httpServer serves the A2A endpoint on a listener.
type httpServer struct {
	server *Server
}

// serve accepts connections until the listener is closed.
func (h *httpServer) serve(ln net.Listener) error {
	for {
		conn, err := ln.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return nil
			}
			return err
		}
		go h.handleConn(conn)
	}
}

// handleConn serves one connection, honouring keep-alive.
func (h *httpServer) handleConn(conn net.Conn) {
	defer conn.Close()
	reader := bufio.NewReaderSize(conn, 16*1024)
	writer := bufio.NewWriter(conn)
	for {
		_ = conn.SetReadDeadline(time.Now().Add(idleTimeout))
		req, err := readRequest(reader)
		if err != nil {
			if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
				return
			}
			// Malformed request line or headers: answer 400 with a legal
			// Absorption Result and close.
			writeJSON(writer, 400, reject(reservedUnknown, "invalid json"))
			_ = writer.Flush()
			return
		}
		status, payload, closeAfter := h.server.dispatch(req)
		body, err := h.server.marshalJSON(payload)
		if err != nil {
			status = 500
			body = []byte(`{"packet_ref":"unknown","status":"rejected","reason":"internal_error: message handler failed"}`)
		}
		writeBody(writer, status, body)
		if err := writer.Flush(); err != nil {
			return
		}
		if closeAfter || strings.EqualFold(req.header("Connection"), "close") {
			return
		}
	}
}

// readRequest parses the request line, the headers and (when the announced
// length is acceptable) the body.
func readRequest(reader *bufio.Reader) (*request, error) {
	line, err := readLine(reader, maxHeaderBytes)
	if err != nil {
		return nil, err
	}
	for line == "" { // tolerate leading empty lines between requests
		line, err = readLine(reader, maxHeaderBytes)
		if err != nil {
			return nil, err
		}
	}
	parts := strings.SplitN(line, " ", 3)
	if len(parts) != 3 {
		return nil, fmt.Errorf("malformed request line")
	}
	req := &request{method: parts[0], path: parts[1], proto: parts[2], headers: map[string]string{}}
	total := len(line)
	for {
		headerLine, err := readLine(reader, maxHeaderBytes)
		if err != nil {
			return nil, err
		}
		total += len(headerLine) + 2
		if total > maxHeaderBytes {
			return nil, fmt.Errorf("headers too large")
		}
		if headerLine == "" {
			break
		}
		colon := strings.IndexByte(headerLine, ':')
		if colon <= 0 {
			return nil, fmt.Errorf("malformed header line")
		}
		name := strings.ToLower(strings.TrimSpace(headerLine[:colon]))
		value := strings.TrimSpace(headerLine[colon+1:])
		if prev, ok := req.headers[name]; ok {
			req.headers[name] = prev + ", " + value
		} else {
			req.headers[name] = value
		}
	}
	// The body is read only when the announced length is a non-negative
	// decimal integer within the limit; otherwise the caller answers 413
	// without touching the body.
	length, ok := parseContentLength(req.header("Content-Length"))
	if !ok || length > maxBodyBytes {
		return req, nil
	}
	body := make([]byte, length)
	if _, err := io.ReadFull(reader, body); err != nil {
		return nil, err
	}
	req.body = body
	return req, nil
}

// readLine reads one CRLF- or LF-terminated line without the terminator.
func readLine(reader *bufio.Reader, limit int) (string, error) {
	var buf []byte
	for {
		chunk, isPrefix, err := reader.ReadLine()
		if err != nil {
			return "", err
		}
		buf = append(buf, chunk...)
		if len(buf) > limit {
			return "", fmt.Errorf("line too long")
		}
		if !isPrefix {
			return string(buf), nil
		}
	}
}

// writeJSON writes a JSON response with the given status.
func writeJSON(writer *bufio.Writer, status int, payload any) {
	body, err := marshalJSON(payload)
	if err != nil {
		status = 500
		body = []byte(`{"packet_ref":"unknown","status":"rejected","reason":"internal_error: message handler failed"}`)
	}
	writeBody(writer, status, body)
}

// writeBody writes a response with an already serialised JSON body.
func writeBody(writer *bufio.Writer, status int, body []byte) {
	fmt.Fprintf(writer, "HTTP/1.1 %d %s\r\n", status, statusText(status))
	fmt.Fprintf(writer, "Content-Type: application/json\r\n")
	fmt.Fprintf(writer, "Content-Length: %d\r\n", len(body))
	if status == 413 {
		fmt.Fprintf(writer, "Connection: close\r\n")
	}
	fmt.Fprintf(writer, "\r\n")
	_, _ = writer.Write(body)
}

// statusText returns the reason phrase for a status code.
func statusText(status int) string {
	switch status {
	case 200:
		return "OK"
	case 400:
		return "Bad Request"
	case 404:
		return "Not Found"
	case 405:
		return "Method Not Allowed"
	case 413:
		return "Request Entity Too Large"
	case 500:
		return "Internal Server Error"
	default:
		return "Status " + strconv.Itoa(status)
	}
}
