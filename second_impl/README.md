# second_impl: an independent minimal implementation

A second, independent implementation of AI-oriented A2A v0.3 over the HTTP binding, written from [SPEC.md](../SPEC.md) and [docs/TRANSPORT.md](../docs/TRANSPORT.md) only. It exists so the protocol does not depend on a single codebase: if it and the reference disagree, one of them (or the spec) has a bug.

- Standard library only. JSON Schema checks use the optional `jsonschema` package (`pip install "ai-oriented-a2a[schema]"` or `pip install jsonschema`). Without it, schema checks are skipped with one warning on stderr.
- Python 3.8+.
- It does **not** import `ai_oriented_a2a` or anything under `examples/`. `tests/interop/test_second_impl.py` enforces this with an AST scan and by running a copy in an isolated interpreter where those imports fail.
- It reads the published schemas from the repository's `schema/` directory (no copy). Set `A2A_SCHEMA_DIR` to use another directory.
- It is not part of the `ai-oriented-a2a` wheel.

**This receiver never returns `absorbed`.** It runs no local regression, so it cannot provide honest `effect_summary` evidence (SPEC §3.1 hard gate). Every Experience Packet that passes the checks and declares a MUST method (`prompt_injection` or `retrieval`) is answered with `status=isolated` and `reason` `isolated: no local regression available`. Other methods are rejected with `unsupported method: <method>`, as SPEC §2 requires.

## Files

| File | Purpose |
| --- | --- |
| `a2a_core.py` | Protocol logic: version check, hard gate, schema check, HMAC-SHA256 sign/verify, replay window, legal rejected results, reply checks |
| `minimal_client.py` | HTTP client (`send`), receiver (`serve`), and CLI |

## Receiver check order

`check_incoming` runs, in order, stopping at the first failure:

1. **version**: `unsupported_version: received <value>` (unknown versions include `"v0.3"`)
2. **hard gate** (Absorption Results only): `hard-gate violation: ...`
3. **schema** (on by default; `--no-schema` turns it off): `schema_invalid: <path>: <message>`
4. **signature** (only with `--sign-key`): `signature verification failed`
5. **replay** (only with `--replay-window`): `replay_or_stale: ...`

Replay runs after the signature check, so an unsigned or forged message cannot use up a nonce. The replay window is keyed on the nonce alone and entries expire after the window.

Every failure is HTTP 200 with a valid Absorption Result: `status=rejected`, a non-empty `packet_ref` (the message `id`, its `packet_ref`, or the `offered_packet.id` of a negotiation message, else `unknown`), and a non-empty `reason`. An unparseable body gives HTTP 400 with `reason` `invalid json`. Reason prefixes are registered in [docs/TRANSPORT.md](../docs/TRANSPORT.md#reserved-reason-prefixes).

Replies to messages that pass the checks:

| Message | Reply |
| --- | --- |
| Experience Packet, method `prompt_injection` or `retrieval` | `isolated`, `isolated: no local regression available` |
| Experience Packet, any other method | `rejected`, `unsupported method: <method>` |
| Experience Packet with `id` `unknown` | `rejected` (the reserved literal cannot be reported) |
| Negotiation (any branch) | `rejected`, `negotiation_unsupported: ...`, `packet_ref` = `offered_packet.id` or `unknown` |
| Absorption Result | `{"ack": true, "type": "result_ack"}` |
| Agent Profile | `{"ack": true, "type": "profile_ack"}` |

With `--sign-key`, every reply is signed.

## CLI

```bash
# receiver
python second_impl/minimal_client.py serve --port 8002 [--sign-key KEY] [--replay-window SECONDS] [--no-schema]

# send one message (packet, Absorption Result, profile, or negotiation)
python second_impl/minimal_client.py send examples/experience-packet.json \
    --url http://127.0.0.1:8002 [--sign-key KEY] [--with-nonce] [--no-schema]
```

`send` checks the message locally (version, hard gate, schema), sends it, checks the reply, and prints the reply as JSON.

- `--with-nonce` attaches a fresh `nonce` and the current integer Unix `timestamp` before signing. Use it when the peer has a replay window enabled.
- `send` has no `--replay-window`. The client never checks replies against a replay window: replies carry no nonce or timestamp.
- With `--sign-key`, a signed reply must verify with the same key.

Exit codes: `0` accepted (isolated, absorbed, or an ack), `1` rejected by the peer, `2` usage error, `3` the peer's reply violates the protocol, `4` transport failure, `5` the local message is invalid.

## Interop with the reference

```bash
# reference receiver, second_impl client
python examples/network_dual_end.py --role receiver --port 8002 --sign-key demo --replay-window 600
python second_impl/minimal_client.py send examples/experience-packet.json \
    --url http://127.0.0.1:8002 --sign-key demo --with-nonce

# second_impl receiver, reference sender
python second_impl/minimal_client.py serve --port 8002 --sign-key demo --replay-window 600
python examples/network_dual_end.py --role sender --port 8001 --peer http://127.0.0.1:8002 \
    --sign-key demo --replay-window 600
```

Both directions are tested in `tests/interop/test_cross_impl.py`. The conformance suite in `tests/conformance/` runs every HTTP case against both receivers; see [docs/COMPLIANCE.md](../docs/COMPLIANCE.md).

## Python API

```python
from second_impl import a2a_core as core, minimal_client as client

reply = client.send("http://127.0.0.1:8002", packet, sign_key="demo", with_nonce=True)
rejection = core.check_incoming(msg, sign_key="demo", replay=core.ReplayWindow(600))
```

`client.send` raises `LocalMessageError`, `TransportError`, or `ReplyProtocolError`. A peer's `rejected` Absorption Result is a normal return value.
