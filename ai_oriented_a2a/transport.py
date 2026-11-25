"""Minimal HTTP binding for AI-oriented A2A v0.4 (standard library only).

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
through the hard gate (``guard_result``) before it is sent. If
``check_incoming`` or the callback raises, the peer gets HTTP 500 and a legal
rejected Absorption Result with the reason
``internal_error: message handler failed`` (no exception text).

Request bodies are capped at ``MAX_BODY_BYTES``. A missing, invalid, or
too-large ``Content-Length`` gets HTTP 413 and a rejected Absorption Result
whose reason starts with ``payload_too_large:``; the body is not read.

The replay window state is one ``ReplayCache`` (seen set + lock) per handler
class, shared by every request thread of the server. ``make_handler`` injects
a fresh one; it is never per request instance, because
``BaseHTTPRequestHandler`` builds a new instance for each request.

The package does not decide absorption: the host's callback does. Packet
``content`` is untrusted data (SPEC §2.1): a callback SHOULD reply with
``ai_oriented_a2a.content_policy_rejection(msg)`` when it is not None, and
MUST wrap content with ``wrap_untrusted`` before any ``prompt_injection``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Set, Tuple, Union

from .protocol import (
    RESERVED_PACKET_REF,
    ReplayCache,
    check_incoming,
    new_id,
    reject_result,
    sign_message,
)

ENDPOINT = "/a2a/message"
DEFAULT_TIMEOUT = 30.0  # docs/TRANSPORT.md: clients SHOULD use >= 30 s
INTERNAL_ERROR_REASON = "internal_error: message handler failed"
INVALID_JSON_REASON = "invalid json"
MAX_BODY_BYTES = 1_048_576
PAYLOAD_TOO_LARGE_REASON = f"payload_too_large: request body exceeds {MAX_BODY_BYTES} bytes"
LENGTH_REQUIRED_REASON = "payload_too_large: missing or invalid Content-Length"
LISTEN_BACKLOG = 128  # socketserver's default of 5 resets bursts of concurrent peers

# Guards the lazy creation of a handler class's ReplayCache.
_CACHE_INIT_LOCK = threading.Lock()

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


def _is_legal_reply(reply: Message) -> bool:
    """True when a handler's return value may be sent as a peer reply.

    SPEC §3.1 requires an Absorption Result after a packet, and TRANSPORT
    promises HTTP 500 with ``internal_error:`` for a handler that returns
    something else. Without this check a callback returning ``None``, a list
    or a bare dict would reach the wire with HTTP 200.
    """
    if not isinstance(reply, dict):
        return False
    if "status" in reply:
        ref = reply.get("packet_ref")
        return reply["status"] in ("absorbed", "rejected", "isolated") and isinstance(ref, str) and bool(ref.strip())
    return reply.get("ack") is True


class A2ARequestHandler(BaseHTTPRequestHandler):
    """``POST /a2a/message`` handler. Override ``handle_message``.

    Configuration is read from class attributes so a subclass (or
    ``make_handler``) can set it: ``sign_key`` enables signature
    verification and signed replies, ``replay_window`` (seconds) enables the
    replay window, and ``seen_nonces`` is the replay state shared by every
    request on the server: a ``ReplayCache`` (set + lock). ``make_handler``
    injects a fresh one. A subclass may also assign a bare set; it is wrapped
    in a ``ReplayCache`` (same set object, one lock) on first use. When left
    as ``None`` each handler class gets its own cache on first use; the base
    class never hands one mutable set to every subclass.
    """

    sign_key: Optional[str] = None
    replay_window: Optional[float] = None
    seen_nonces: Optional[Union[ReplayCache, Set[Tuple[str, str]]]] = None

    def handle_message(self, msg: Message) -> Message:
        """Return the reply for a message that passed ``check_incoming``."""
        raise NotImplementedError("override handle_message or use make_handler(on_message)")

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if self.path != ENDPOINT:
            self.send_error(404)
            return
        length = _content_length(self.headers.get("Content-Length"))
        if length is None:
            self._reject_payload(LENGTH_REQUIRED_REASON)
            return
        if length > MAX_BODY_BYTES:
            self._reject_payload(PAYLOAD_TOO_LARGE_REASON)
            return
        body = self.rfile.read(length)
        if len(body) > MAX_BODY_BYTES:  # defensive: never trust a short read
            self._reject_payload(PAYLOAD_TOO_LARGE_REASON)
            return
        try:
            msg = json.loads(body.decode())
            if not isinstance(msg, dict):
                raise ValueError("message must be a JSON object")
        except Exception:
            self._reply(reject_result(None, INVALID_JSON_REASON), status=400)
            return

        try:
            rejected = check_incoming(
                msg,
                sign_key=self.sign_key,
                replay_window=self.replay_window,
                seen=self._replay_cache() if self.replay_window is not None else None,
            )
            reply = rejected if rejected else guard_result(self.handle_message(msg))
            if not _is_legal_reply(reply):
                log.error(
                    "A2A message handler returned %s, not an Absorption Result or ack; replying 500",
                    type(reply).__name__,
                )
                self._reply(reject_result(msg, INTERNAL_ERROR_REASON), status=500)
                return
        except Exception:
            # Log locally; never send exception text, paths, or tracebacks.
            log.exception("A2A message handler failed")
            self._reply(reject_result(msg, INTERNAL_ERROR_REASON), status=500)
            return
        self._reply(reply)

    def _replay_cache(self) -> ReplayCache:
        """Return this handler class's shared ReplayCache, creating it once."""
        cls = type(self)
        cache = cls.seen_nonces
        if isinstance(cache, ReplayCache):
            return cache
        with _CACHE_INIT_LOCK:
            cache = cls.seen_nonces
            if not isinstance(cache, ReplayCache):
                cache = ReplayCache(cache if isinstance(cache, set) else None)
                cls.seen_nonces = cache
            return cache

    def _reject_payload(self, reason: str) -> None:
        # The body was not read; close the connection so leftover bytes are
        # never parsed as a new request.
        self.close_connection = True
        self._reply(reject_result(None, reason), status=413)

    def _reply(self, payload: Message, status: int = 200) -> None:
        if self.sign_key:
            payload = sign_message(payload, self.sign_key)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _content_length(raw: Optional[str]) -> Optional[int]:
    """Parse a Content-Length header; None if missing or not a non-negative int."""
    if raw is None:
        return None
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):  # rejects "", "-1", "+1", "1.0", "0x10"
        return None
    try:
        return int(text)
    except ValueError:  # e.g. more digits than int() accepts
        return None


def make_handler(
    on_message: MessageHandler,
    *,
    sign_key: Optional[str] = None,
    replay_window: Optional[float] = None,
) -> type:
    """Return an ``A2ARequestHandler`` subclass that calls ``on_message``.

    Each returned class has its own ``ReplayCache`` (replay set + lock),
    shared by every request the server handles.
    """
    callback = on_message

    class _Handler(A2ARequestHandler):
        def handle_message(self, msg: Message) -> Message:
            return callback(msg)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log.debug("%s - %s", self.address_string(), format % args)

    _Handler.sign_key = sign_key
    _Handler.replay_window = replay_window
    _Handler.seen_nonces = ReplayCache()
    return _Handler


class _ThreadingA2AServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` with a listen backlog that fits bursts.

    ``socketserver`` defaults to a backlog of 5, so a burst of concurrent
    peers can see connection resets before a request thread even starts.
    """

    daemon_threads = True
    request_queue_size = LISTEN_BACKLOG


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
    stop it with ``server.shutdown()`` and ``server.server_close()``. The
    listen backlog is ``LISTEN_BACKLOG`` (128) rather than socketserver's 5.
    """
    handler = make_handler(on_message, sign_key=sign_key, replay_window=replay_window)
    return _ThreadingA2AServer((host, port), handler)


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
    "MAX_BODY_BYTES",
    "ReplayCache",
    "TransportError",
    "guard_result",
    "make_handler",
    "make_server",
    "send_message",
    "serve",
]
