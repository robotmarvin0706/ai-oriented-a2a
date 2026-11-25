# mcp_a2a: MCP bridge

An MCP server that lets any MCP host (Claude Desktop, IDE agents, ...) act as an AI-oriented A2A v0.4 **sender** without implementing the protocol, plus a **local** experience lookup. It never receives packets: `send_packet` and `report_absorption` go out to a peer, and `query_experience` reads a local corpus.

The protocol logic comes from [`second_impl/`](../second_impl/README.md): local version / hard-gate / schema checks, a fresh `nonce` and `timestamp` on every message, mandatory HMAC-SHA256 signing, and validation of the peer's reply.

## Requirements

- Python ≥ 3.10 (the core package still supports 3.8).
- The official MCP Python SDK 2.x, via the optional extra, from a checkout of this repository:

```bash
pip install -e ".[schema,mcp]"     # mcp>=2,<3 and jsonschema
```

The bridge is not part of the wheel; run it from the checkout.

## Tools

| Tool | Arguments | Returns |
| --- | --- | --- |
| `send_packet` | `packet` (Experience Packet object), `peer_url` (optional) | The peer's Absorption Result, unchanged |
| `report_absorption` | `result` (Absorption Result object), `peer_url` (optional) | The peer's reply, unchanged: an ack such as `{"ack": true, "type": "result_ack"}`, or a rejected Absorption Result |
| `query_experience` | `query` (what went wrong), `limit` (default 5), `max_tokens` (default 800) | `{"items": [...], "blocked": [...], "truncated": bool, ...}` from the local corpus |

`query_experience` never touches the network and needs no peer URL. Every candidate goes through the SPEC §2.1 content check first: a packet that matches a forbidden directive pattern is listed in `blocked` with its reason and its content is never returned. Returned content is **untrusted data** (SPEC §2.1): put it below your system and host instructions, never in the system prompt, and absorb it only through your own gate. Each item carries `"untrusted": true` and the reply repeats the notice, because a corpus is not a trust anchor.

A peer's `rejected` result is a protocol answer and is returned normally. These raise an MCP tool error (`isError: true`, with the reason in the text):

- `A2A_SIGN_KEY` is unset or blank (the bridge signs every outbound message and has no unsigned mode), so nothing is sent;
- the local message is invalid (unknown version, hard-gate violation such as `absorbed` without `effect_summary.note`, schema failure, wrong message type);
- the peer is not allowed;
- transport failure (connection refused, timeout, non-JSON reply);
- the reply violates the protocol (bad reply signature, `packet_ref` mismatch, invalid Absorption Result).

## Configuration

All settings come from environment variables, read on every call.

| Variable | Meaning |
| --- | --- |
| `A2A_PEER_URL` | Default peer base URL (`/a2a/message` is appended). Always allowed. |
| `A2A_ALLOWED_PEERS` | Comma-separated base URLs a tool call may target with `peer_url`. Anything else is refused. If neither this nor `A2A_PEER_URL` is set, every call is refused. |
| `A2A_SIGN_KEY` | HMAC-SHA256 key for signing and for verifying signed replies. **Required**: missing or blank is a tool error and nothing is sent. Environment only; it is never a tool argument, so the model never sees it. |
| `A2A_EXPERIENCE_DIR` | Directory of Experience Packet JSON files for `query_experience` (default: `datasets/timeout_recovery/packets` in a checkout). |
| `A2A_NO_SCHEMA` | `1` to skip JSON Schema checks. |
| `A2A_SCHEMA_DIR` | Use schemas from another directory instead of the repository's `schema/`. |

The allowlist keeps a model from pointing the bridge at arbitrary URLs (for example internal services), and it is checked before the key is read. Signing is not optional: a misconfigured host fails loudly instead of quietly sending unauthenticated packets.

## Mounting it

### Claude Desktop

Edit `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "ai-oriented-a2a": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/ai-oriented-a2a/mcp_a2a/server.py"],
      "env": {
        "A2A_PEER_URL": "http://127.0.0.1:8002",
        "A2A_ALLOWED_PEERS": "http://127.0.0.1:8002",
        "A2A_SIGN_KEY": "change-me"
      }
    }
  }
}
```

Use the Python from the environment where you installed `.[schema,mcp]`. `A2A_SIGN_KEY` is mandatory (without it every outbound call fails). That file is plain text, so prefer a key used only for this peer.

### Any stdio MCP host

The server speaks MCP over stdio. Any host that launches a command works:

```bash
A2A_PEER_URL=http://127.0.0.1:8002 A2A_SIGN_KEY=dev-key python mcp_a2a/server.py
```

Most hosts take the same `command` / `args` / `env` triple as above. Logs go to stderr; stdout is the MCP wire.

## Try it locally

```bash
python second_impl/minimal_client.py serve --port 8002        # or examples/network_dual_end.py --role receiver
A2A_PEER_URL=http://127.0.0.1:8002 A2A_SIGN_KEY=dev-key mcp dev mcp_a2a/server.py  # MCP Inspector, needs "mcp[cli]"
```

Tests: `tests/mcp_bridge/test_bridge.py` (in-memory MCP client, local receivers only) and `tests/mcp_bridge/test_lookup.py` (corpus ranking, the §2.1 block list, the token budget, and the signing guard; no MCP SDK needed). CI runs the bridge tests in the separate, non-required `mcp-bridge` job; the lookup tests run everywhere.
