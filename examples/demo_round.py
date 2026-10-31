#!/usr/bin/env python3
"""
Non-normative demonstration of one exchange round.
Reads examples/experience-packet.json, prints an Absorption Result.
packet_ref uses the packet's id.
The rejection on prompt_injection is a demo constraint only, not a protocol prohibition.
"""

import json
from pathlib import Path


def main():
    packet = json.loads(Path("examples/experience-packet.json").read_text())
    print("=== Received Packet ===")
    print(json.dumps(packet, indent=2, ensure_ascii=False))

    method = packet.get("absorption", {}).get("method", "")
    # Demo-only rule: reject prompt_injection to show explicit rejection path
    if method == "prompt_injection":
        result = {
            "packet_ref": packet["id"],
            "status": "rejected",
            "reason": "demo constraint: absorption.method prompt_injection not executed in this run",
        }
    else:
        result = {
            "packet_ref": packet["id"],
            "status": "absorbed",
        }

    print("\n=== Absorption Result ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
