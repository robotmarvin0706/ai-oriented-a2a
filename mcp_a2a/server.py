#!/usr/bin/env python3
"""One-way MCP -> A2A bridge (stdio).

Exposes two MCP tools that let an MCP host act as an A2A v0.3 sender:

- ``send_packet(packet, peer_url=None)`` sends an Experience Packet and
  returns the peer's Absorption Result unchanged.
- ``report_absorption(result, peer_url=None)`` sends an Absorption Result
  to the peer that sent a packet and returns the peer's reply unchanged
  (an ack, or a rejected Absorption Result).

A peer's ``rejected`` Absorption Result is a protocol-level answer and is
returned, not raised. Local validation failures, network failures, and
replies that violate SPEC/TRANSPORT raise ``ToolError``, which the MCP SDK
reports as a tool error (``isError: true``) with the message text.

"One-way" means outbound only: the bridge does not receive packets.

Requires Python >= 3.10 and the optional extra: pip install "ai-oriented-a2a[mcp]"
(that is, ``mcp>=2,<3``). The protocol logic comes from ``second_impl``.

Environment (read on every call):

- ``A2A_PEER_URL``: default peer base URL, also allowed implicitly.
- ``A2A_ALLOWED_PEERS``: comma-separated base URLs a tool may target.
  A ``peer_url`` outside this list and ``A2A_PEER_URL`` is refused.
- ``A2A_SIGN_KEY``: HMAC-SHA256 key. Read from the environment only;
  it is never a tool argument.
- ``A2A_NO_SCHEMA=1``: skip JSON Schema checks.
- ``A2A_SCHEMA_DIR``: schema directory override (see second_impl).
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from second_impl import a2a_core as core  # noqa: E402
from second_impl import minimal_client as client  # noqa: E402

log = logging.getLogger("mcp_a2a")

mcp = MCPServer(
    "ai-oriented-a2a",
    instructions=(
        "Send AI-oriented A2A v0.3 Experience Packets to a configured peer and "
        "report Absorption Results back. Peers are limited to A2A_PEER_URL and "
        "A2A_ALLOWED_PEERS."
    ),
)


class PeerNotAllowedError(ToolError):
    """peer_url is not configured as an allowed peer."""


class A2AToolError(ToolError):
    """Local validation, transport, or reply-protocol failure, surfaced as an MCP tool error."""


def _normalize(url: str) -> str:
    return url.strip().rstrip("/")


def allowed_peers() -> set[str]:
    peers = {_normalize(u) for u in os.environ.get("A2A_ALLOWED_PEERS", "").split(",") if u.strip()}
    default = os.environ.get("A2A_PEER_URL", "").strip()
    if default:
        peers.add(_normalize(default))
    return peers


def resolve_peer(peer_url: str | None) -> str:
    allowed = allowed_peers()
    if not allowed:
        raise PeerNotAllowedError("no peers configured: set A2A_PEER_URL or A2A_ALLOWED_PEERS")
    target = peer_url or os.environ.get("A2A_PEER_URL", "")
    if not target.strip():
        raise PeerNotAllowedError("peer_url not given and A2A_PEER_URL is not set")
    if _normalize(target) not in allowed:
        raise PeerNotAllowedError(f"peer_url {target!r} is not in A2A_PEER_URL / A2A_ALLOWED_PEERS")
    return _normalize(target)


def _send(msg: dict[str, Any], peer_url: str | None) -> dict[str, Any]:
    target = resolve_peer(peer_url)
    sign_key = os.environ.get("A2A_SIGN_KEY") or None
    schema = os.environ.get("A2A_NO_SCHEMA", "") not in ("1", "true", "yes")
    try:
        return client.send(target, msg, sign_key=sign_key, with_nonce=True, schema=schema)
    except client.LocalMessageError as exc:
        raise A2AToolError(f"local message invalid: {exc}") from exc
    except client.TransportError as exc:
        raise A2AToolError(f"transport failure: {exc}") from exc
    except client.ReplyProtocolError as exc:
        raise A2AToolError(f"peer reply violates the protocol: {exc}") from exc


@mcp.tool()
def send_packet(packet: dict[str, Any], peer_url: str | None = None) -> dict[str, Any]:
    """Send an A2A v0.3 Experience Packet to a peer and return its Absorption Result.

    packet: the Experience Packet (id, type, content, absorption, ...). A fresh
    nonce and timestamp are attached, and it is signed if A2A_SIGN_KEY is set.
    peer_url: peer base URL; defaults to A2A_PEER_URL and must be an allowed peer.
    Returns the peer's Absorption Result as-is (status absorbed, rejected, or isolated).
    """
    if not isinstance(packet, dict) or core.kind_of(packet) != "experience-packet":
        raise A2AToolError("local message invalid: send_packet expects an Experience Packet object")
    return _send(packet, peer_url)


@mcp.tool()
def report_absorption(result: dict[str, Any], peer_url: str | None = None) -> dict[str, Any]:
    """Send an A2A v0.3 Absorption Result to a peer and return the peer's reply.

    result: the Absorption Result (packet_ref, status, reason / effect_summary).
    status=absorbed requires a non-empty effect_summary.note (SPEC §3.1 hard gate).
    peer_url: peer base URL; defaults to A2A_PEER_URL and must be an allowed peer.
    Returns the peer's reply as-is: an ack, or a rejected Absorption Result.
    """
    if not core.is_absorption_result(result):
        raise A2AToolError("local message invalid: report_absorption expects an Absorption Result object")
    return _send(result, peer_url)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
