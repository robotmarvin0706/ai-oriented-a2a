"""Minimal HTTP binding for AI-oriented A2A v0.3 (standard library only).

Import it explicitly; it is not re-exported from the top-level package, so
``import ai_oriented_a2a`` does not load ``http.server`` or ``urllib``::

    from ai_oriented_a2a.transport import send_message, make_server, serve

Client: ``send_message(url, message, *, timeout=30.0, sign_key=None,
add_replay_fields=False)`` POSTs one message to ``url + "/a2a/message"`` and
returns the peer's JSON reply.

Server: subclass ``A2ARequestHandler`` and override ``handle_message``, or
pass a callback to ``make_handler`` / ``make_server`` / ``serve``. The
handler does everything docs/TRANSPORT.md requires before your code runs:
404 for other paths, HTTP 400 + a legal Absorption Result for invalid JSON,
then ``check_incoming`` (version first, optional signature and replay
window). Your callback only sees messages that passed. Its result is passed
through the hard gate (``guard_result``) before it is sent. If the callback
raises, the peer gets HTTP 500 and a legal rejected Absorption Result with
the reason ``internal_error: message handler failed`` (no exception text).

The package does not decide absorption: the host's callback does.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Set, Tuple

from .protocol import (
    RESERVED_PACKET_REF,
    check_incoming,
    new_id,
    reject_result,
    sign_message,
)

ENDPOINT = "/a2a/message"
DEFAULT_TIMEOUT = 30.0  # docs/TRANSPORT.md: clients SHOULD use >= 30 s
INTERNAL_ERROR_REASON = "internal_error: message handler failed"
INVALID_JSON_REASON = "invalid json"

Message = Dict[str, Any]
MessageHandler = Callable[[Message], Message]

log = logging.getLogger(__name__)


class TransportError(Exception):
    """Network failure, timeout, or a reply body that is not a JSON object."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def send_message(
    url: str,
    message: Message,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    sign_key: Optional[str] = None,
    add_replay_fields: bool = False,
) -> Message:
    """POST ``message`` to ``url`` + ``/a2a/message`` and return the JSON reply.

    ``add_replay_fields`` attaches a fresh ``nonce`` and the current integer
    ``timestamp`` (needed when the peer has a replay window enabled).
    ``sign_key`` signs the message last (HMAC-SHA256 over canonical JSON).

    An HTTP error status whose body is a JSON object (for example the 400
    ``invalid json`` Absorption Result) is returned like a normal reply.
    Raises TransportError on network errors, timeouts, or a non-JSON body.
    The reply is not validated; a peer's ``rejected`` result is a normal
    return value.
    """
    if add_replay_fields:
        message = dict(message)
        message["nonce"] = new_id("nonce")
        message["timestamp"] = int(time.time())
    if sign_key:
        message = sign_message(message, sign_key)
    data = json.dumps(message).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise TransportError(f"cannot reach {url}: {exc}") from exc
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TransportError("reply body is not JSON") from exc
    if not isinstance(body, dict):
        raise TransportError("reply body is not a JSON object")
    return body


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def guard_result(result: Message) -> Message:
    """Apply the SPEC §3.1 hard gate to a reply before it is sent.

    ``status=absorbed`` without a non-empty ``effect_summary.note``, or with
    the reserved ``packet_ref`` ``unknown``, becomes a rejected result.
    Anything else (other statuses, acks) is returned unchanged.
    """
    if not isinstance(result, dict) or result.get("status") != "absorbed":
        return result
    if result.get("packet_ref") == RESERVED_PACKET_REF:
        return reject_result(
            result, "reserved packet_ref 'unknown' cannot be used for successful absorption"
        )
    summary = result.get("effect_summary")
    note = summary.get("note") if isinstance(summary, dict) else None
    if not (isinstance(note, str) and note.strip()):
        return reject_result(
            result, "hard-gate violation: absorbed result missing non-empty effect_summary.note"
        )
    return result


class A2ARequestHandler(BaseHTTPRequestHandler):
    """``POST /a2a/message`` handler. Override ``handle_message``.

    Configuration is read from class attributes so a subclass (or
    ``make_handler``) can set it: ``sign_key`` enables signature
    verification and signed replies, ``replay_window`` (seconds) enables the
    replay window, and ``seen_nonces`` is the shared replay set.
    """

    sign_key: Optional[str] = None
    replay_window: Optional[float] = None
    seen_nonces: Set[Tuple[str, str]] = set()

    def handle_message(self, msg: Message) -> Message:
        """Return the reply for a message that passed ``check_incoming``."""
        raise NotImplementedError("override handle_message or use make_handler(on_message)")

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if self.path != ENDPOINT:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length)
        try:
            msg = json.loads(body.decode())
            if not isinstance(msg, dict):
                raise ValueError("message must be a JSON object")
        except Exception:
            self._reply(reject_result(None, INVALID_JSON_REASON), status=400)
            return

        rejected = check_incoming(
            msg,
            sign_key=self.sign_key,
            replay_window=self.replay_window,
            seen=self.seen_nonces,
        )
        if rejected:
            self._reply(rejected)
            return

        try:
            reply = guard_result(self.handle_message(msg))
        except Exception:
            # Log locally; never send exception text, paths, or tracebacks.
            log.exception("A2A message handler failed")
            self._reply(reject_result(msg, INTERNAL_ERROR_REASON), status=500)
            return
        self._reply(reply)

    def _reply(self, payload: Message, status: int = 200) -> None:
        if self.sign_key:
            payload = sign_message(payload, self.sign_key)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_handler(
    on_message: MessageHandler,
    *,
    sign_key: Optional[str] = None,
    replay_window: Optional[float] = None,
) -> type:
    """Return an ``A2ARequestHandler`` subclass that calls ``on_message``.

    Each returned class has its own replay set.
    """
    callback = on_message

    class _Handler(A2ARequestHandler):
        def handle_message(self, msg: Message) -> Message:
            return callback(msg)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log.debug("%s - %s", self.address_string(), format % args)

    _Handler.sign_key = sign_key
    _Handler.replay_window = replay_window
    _Handler.seen_nonces = set()
    return _Handler


def make_server(
    on_message: MessageHandler,
    port: int = 0,
    host: str = "127.0.0.1",
    *,
    sign_key: Optional[str] = None,
    replay_window: Optional[float] = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) a threaded server; ``port=0`` picks a free port.

    Start it with ``server.serve_forever()`` (for example in a thread) and
    stop it with ``server.shutdown()`` and ``server.server_close()``.
    """
    handler = make_handler(on_message, sign_key=sign_key, replay_window=replay_window)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(
    on_message: MessageHandler,
    port: int,
    host: str = "127.0.0.1",
    *,
    sign_key: Optional[str] = None,
    replay_window: Optional[float] = None,
) -> None:
    """Serve ``POST /a2a/message`` until interrupted (blocks)."""
    server = make_server(on_message, port, host, sign_key=sign_key, replay_window=replay_window)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = [
    "A2ARequestHandler",
    "DEFAULT_TIMEOUT",
    "ENDPOINT",
    "INTERNAL_ERROR_REASON",
    "TransportError",
    "guard_result",
    "make_handler",
    "make_server",
    "send_message",
    "serve",
]
