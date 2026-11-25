#!/usr/bin/env python3
"""Minimal independent A2A v0.4 client and receiver (HTTP binding).

Standard library only (plus optional ``jsonschema`` for schema checks).
Does not import ``ai_oriented_a2a`` or ``examples/``.

  python second_impl/minimal_client.py send PACKET.json --url http://127.0.0.1:8002 \
      [--sign-key KEY] [--with-nonce] [--no-schema]
  python second_impl/minimal_client.py serve --port 8002 \
      [--sign-key KEY] [--replay-window SECONDS] [--no-schema] \
      [--core-constraint TEXT ...] [--max-rounds N]

``send`` exit codes: 0 accepted (isolated, absorbed, or an ack),
1 rejected by the peer, 3 the peer's reply violates the protocol,
4 transport failure, 5 the local message is invalid. Usage errors exit 2.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__:
    from . import a2a_core as core
else:  # run as a script: python second_impl/minimal_client.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import a2a_core as core  # type: ignore[no-redef]

ENDPOINT = "/a2a/message"

EXIT_ACCEPTED = 0
EXIT_REJECTED = 1
EXIT_REPLY_INVALID = 3
EXIT_TRANSPORT = 4
EXIT_LOCAL_INVALID = 5


class TransportError(Exception):
    """Network failure, timeout, or a reply body that is not JSON."""


class ReplyProtocolError(Exception):
    """The peer replied, but the reply violates SPEC/TRANSPORT."""

    def __init__(self, problem: str, body: Any) -> None:
        super().__init__(problem)
        self.body = body


class LocalMessageError(ValueError):
    """The message we were asked to send is invalid."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def post(
    url: str,
    msg: Dict[str, Any],
    *,
    sign_key: Optional[str] = None,
    with_nonce: bool = False,
    timeout: float = core.CLIENT_TIMEOUT_SECONDS,
) -> Tuple[int, Any, Dict[str, Any]]:
    """POST ``msg`` to ``url`` + /a2a/message.

    Returns ``(http_status, parsed_body, sent_message)``. ``with_nonce``
    attaches a fresh nonce and timestamp before signing. Raises
    TransportError on network errors or a non-JSON body.
    """
    if with_nonce:
        msg = core.fresh_replay_fields(msg)
    if sign_key:
        msg = core.sign(msg, sign_key)
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(request, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise TransportError(
                f"peer redirected ({exc.code}) to {exc.headers.get('Location')!r}; redirects are not followed"
            ) from exc
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise TransportError(f"cannot reach {url}: {exc}") from exc
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TransportError(f"HTTP {status}: reply body is not JSON") from exc
    return status, body, msg


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects; see ai_oriented_a2a.transport (audit P1-7)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def send(
    url: str,
    msg: Any,
    *,
    sign_key: Optional[str] = None,
    with_nonce: bool = False,
    schema: bool = True,
    timeout: float = core.CLIENT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Check, send, and validate the reply. Returns the peer's reply.

    The reply is an Absorption Result (for a packet or a negotiation
    message) or an ack (for a profile or a pushed Absorption Result).
    Raises LocalMessageError, TransportError, or ReplyProtocolError.
    """
    problem = core.check_outgoing(msg, schema=schema)
    if problem:
        raise LocalMessageError(problem)
    status, body, sent = post(url, msg, sign_key=sign_key, with_nonce=with_nonce, timeout=timeout)
    problem = core.check_reply(sent, body, sign_key=sign_key, schema=schema)
    if problem is None and status != 200 and not (status == 400 and core.is_absorption_result(body)):
        problem = f"unexpected HTTP status {status}"
    if problem:
        raise ReplyProtocolError(problem, body)
    return body


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------


class _Server(ThreadingHTTPServer):
    # socketserver's default backlog of 5 resets bursts of concurrent peers.
    daemon_threads = True
    request_queue_size = 128


def _content_length(raw: Optional[str]) -> Optional[int]:
    """Content-Length as a non-negative int, or None if missing or invalid."""
    if raw is None:
        return None
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def make_server(
    port: int = 0,
    *,
    host: str = "127.0.0.1",
    sign_key: Optional[str] = None,
    replay_window: Optional[float] = None,
    schema: bool = True,
    log: bool = False,
    core_constraints: Optional[List[str]] = None,
    round_cap: int = core.DEFAULT_ROUND_CAP,
    peer_rate_limit: Optional[int] = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) a receiver for POST /a2a/message.

    ``server.received`` lists the messages that passed every check.
    Bodies above ``core.MAX_BODY_BYTES``, or with a missing or invalid
    ``Content-Length``, get HTTP 413 and ``payload_too_large:``. An
    unexpected error in the checks or the reply gets HTTP 500 and
    ``internal_error: message handler failed``.

    Negotiation (SPEC §3): ``core_constraints`` are this receiver's
    immutable constraints; ``round_cap`` caps messages per
    ``negotiation_id``; ``peer_rate_limit`` (negotiation messages per minute
    per client address) is off when None.
    """
    replay = core.ReplayWindow(replay_window) if replay_window is not None else None
    cores = list(core_constraints or [])
    limits = core.NegotiationLimits(round_cap, per_peer=peer_rate_limit)
    received: List[Dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            if log:
                sys.stderr.write("[second_impl] %s\n" % (format % args))

        def do_POST(self) -> None:  # noqa: N802
            if self.path != ENDPOINT:
                self.send_error(404)
                return
            length = _content_length(self.headers.get("Content-Length"))
            if length is None or length > core.MAX_BODY_BYTES:
                detail = (
                    "missing or invalid Content-Length"
                    if length is None
                    else f"request body exceeds {core.MAX_BODY_BYTES} bytes"
                )
                # Do not read the body; close so leftover bytes are not parsed.
                self.close_connection = True
                self._reply(core.rejected(None, f"{core.REASON_PAYLOAD_TOO_LARGE}: {detail}"), 413)
                return
            raw = self.rfile.read(length)
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                msg = None
            if not isinstance(msg, dict):
                self._reply(core.rejected(None, core.REASON_INVALID_JSON), 400)
                return
            try:
                # SPEC §2.1: untrusted content is screened before any other
                # decision, so a directive packet with a malformed id gets
                # content_policy: here and not schema_invalid.
                problem = (
                    core.content_problem(msg)
                    if core.kind_of(msg) in ("experience-packet", "negotiation")
                    else None
                )
                if problem is not None:
                    received.append(msg)
                    reply = {"packet_ref": problem[0], "status": "rejected", "reason": problem[1]}
                    self._reply(reply)
                    return
                rejection = core.check_incoming(msg, sign_key=sign_key, replay=replay, schema=schema)
                if rejection is None:
                    received.append(msg)
                reply = rejection if rejection else core.respond(
                    msg, core_constraints=cores, limits=limits, peer=self.client_address[0]
                )
            except Exception:
                # No exception text, path, or traceback on the wire.
                if log:
                    sys.stderr.write("[second_impl] message handler failed\n")
                self._reply(core.rejected(msg, core.REASON_INTERNAL), 500)
                return
            self._reply(reply)

        def _reply(self, payload: Dict[str, Any], status: int = 200) -> None:
            if sign_key:
                payload = core.sign(payload, sign_key)
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = _Server((host, port), Handler)
    server.received = received  # type: ignore[attr-defined]
    return server


def start_in_thread(**kwargs: Any) -> Tuple[ThreadingHTTPServer, str]:
    """Start a receiver on a free port in a daemon thread; return (server, base_url)."""
    server = make_server(**kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SEND_HELP = (
    "Send one message (Experience Packet, Absorption Result, Agent Profile, "
    "or negotiation) and print the peer's reply. The client never checks "
    "replies against a replay window: replies carry no nonce or timestamp. "
    "There is no --replay-window option on send; use --with-nonce when the "
    "peer has a replay window enabled."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minimal_client.py",
        description="Independent minimal client and receiver for AI-oriented A2A v0.4.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send", help="send one message", description=SEND_HELP)
    p_send.add_argument("message", help="path to a JSON message file")
    p_send.add_argument("--url", required=True, help="peer base URL; /a2a/message is appended")
    p_send.add_argument("--sign-key", default=None, help="HMAC-SHA256 key: sign the message and verify a signed reply")
    p_send.add_argument(
        "--with-nonce",
        action="store_true",
        help="attach a fresh nonce and the current Unix timestamp before signing "
        "(needed when the peer has a replay window enabled)",
    )
    p_send.add_argument("--no-schema", action="store_true", help="skip JSON Schema checks of the message and the reply")
    p_send.add_argument("--timeout", type=float, default=core.CLIENT_TIMEOUT_SECONDS, help="seconds (default 30)")

    p_serve = sub.add_parser("serve", help="run a receiver on POST /a2a/message")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8002)
    p_serve.add_argument("--sign-key", default=None, help="enable HMAC-SHA256 verification and sign replies")
    p_serve.add_argument(
        "--replay-window",
        type=float,
        default=None,
        metavar="SECONDS",
        help="enable the replay window: incoming messages must carry nonce and timestamp",
    )
    p_serve.add_argument("--no-schema", action="store_true", help="skip JSON Schema checks of incoming messages")
    p_serve.add_argument(
        "--core-constraint",
        action="append",
        default=[],
        metavar="TEXT",
        help="immutable constraint (repeatable): a propose/counter targeting it gets immutable_constraint:",
    )
    p_serve.add_argument(
        "--max-rounds",
        type=int,
        default=core.DEFAULT_ROUND_CAP,
        help="messages allowed per negotiation_id before negotiation_limit: (default 3)",
    )
    return parser


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        server = make_server(
            args.port,
            host=args.host,
            sign_key=args.sign_key,
            replay_window=args.replay_window,
            schema=not args.no_schema,
            log=True,
            core_constraints=args.core_constraint,
            round_cap=args.max_rounds,
        )
        window = f"{args.replay_window}s" if args.replay_window is not None else "off"
        print(
            f"[second_impl] listening on http://{args.host}:{server.server_address[1]}{ENDPOINT} "
            f"(sign={'on' if args.sign_key else 'off'}, replay={window}, "
            f"schema={'off' if args.no_schema else 'on'})",
            file=sys.stderr,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    try:
        msg = core.load_json_file(args.message)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {args.message}: {exc}", file=sys.stderr)
        return EXIT_LOCAL_INVALID
    try:
        reply = send(
            args.url,
            msg,
            sign_key=args.sign_key,
            with_nonce=args.with_nonce,
            schema=not args.no_schema,
            timeout=args.timeout,
        )
    except LocalMessageError as exc:
        print(f"error: local message invalid: {exc}", file=sys.stderr)
        return EXIT_LOCAL_INVALID
    except TransportError as exc:
        print(f"error: transport: {exc}", file=sys.stderr)
        return EXIT_TRANSPORT
    except ReplyProtocolError as exc:
        _print(exc.body)
        print(f"error: peer reply violates the protocol: {exc}", file=sys.stderr)
        return EXIT_REPLY_INVALID
    _print(reply)
    return EXIT_REJECTED if reply.get("status") == "rejected" else EXIT_ACCEPTED


if __name__ == "__main__":
    sys.exit(main())
