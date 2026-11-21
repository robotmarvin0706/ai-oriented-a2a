"""0.4.1 fixes in the examples: the minimal receiver derives its policy from
packet content, and the network demo seeds with a stable digest."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
for p in (str(REPO), str(EXAMPLES)):
    if p not in sys.path:
        sys.path.insert(0, p)

from minimal_dual_end import Receiver, naive_policy, recovered_policy  # noqa: E402


def _packet(pid, content, method="prompt_injection"):
    return {"id": pid, "type": "failure_mode", "content": content, "absorption": {"method": method}}


WRONG = {
    "trigger": "tool_call_timeout on web_search",
    "recovery_policy": {"backoff": "none", "max_attempts": "unbounded", "drop_stale_partials": False},
    "compressed_signal": "retry immediately without backoff and keep retrying until a result arrives; keep partial results",
}


def test_policy_follows_packet_content():
    recv = Receiver(isolation_n=3)
    content = {"compressed_signal": "exponential backoff, at most 5 attempts"}
    result = recv.absorb(_packet("pkt-five", content), random.Random(1))
    metrics = result["effect_summary"]["metrics"]
    assert metrics["strategy_parsed"] is True
    assert "max_attempts=5" in metrics["policy_after"] and "backoff=True" in metrics["policy_after"]
    if result["status"] == "absorbed":
        assert recv.state.policy.max_attempts == 5


def test_empty_and_unrelated_packets_change_nothing():
    for pid, content in (("pkt-empty", {}), ("pkt-other", {"compressed_signal": "prefer bullet lists in reports"})):
        recv = Receiver(isolation_n=3)
        result = recv.absorb(_packet(pid, content, "retrieval"), random.Random(3))
        assert result["status"] == "rejected"
        assert "no recovery parameters" in result["reason"]
        assert result["effect_summary"]["metrics"]["strategy_parsed"] is False
        assert result["effect_summary"]["metrics"]["rollback_triggered"] is False
        assert recv.state.policy == naive_policy()
        assert recv.state.retrieval_store == {} and recv.state.prompt_prefix == ""


def test_wrong_experience_lowers_sr_and_rolls_back():
    for seed in range(10):
        recv = Receiver(isolation_n=3, regression_drop_threshold=0.10)
        recv.state.policy = recovered_policy()
        before = recv.state.policy
        result = recv.absorb(_packet(f"pkt-wrong-{seed}", WRONG), random.Random(seed), regression_trials=8)
        m = result["effect_summary"]["metrics"]
        assert m["regression_sr"] - m["baseline_sr"] < 0  # ΔSR of the bad advice (8 regression trials)
        assert m["post_change_sr"] - m["baseline_sr"] <= 0
        assert result["status"] == "rejected" and m["rollback_triggered"] is True
        assert recv.state.policy == before


def test_network_demo_seed_is_stable_across_processes():
    code = (
        "import json,random,sys; sys.path[:0]=['examples']; "
        "import network_dual_end as n; from minimal_dual_end import Receiver; "
        "p=json.load(open('examples/experience-packet.json')); "
        "r=Receiver(isolation_n=3, regression_drop_threshold=0.10).absorb(p, random.Random(n.stable_seed(p['id']))); "
        "print(n.stable_seed(p['id']), r['status'])"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code], cwd=REPO, env=dict(os.environ, PYTHONHASHSEED=h),
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        for h in ("0", "1", "4242")
    }
    assert len(outs) == 1
    assert outs.pop().endswith(" absorbed")


def test_example_packet_parses_to_backoff_and_stale_drop():
    from experiments.timeout_recovery.strategy import parse_recovery_params

    content = json.loads((EXAMPLES / "experience-packet.json").read_text())["content"]
    params, _ = parse_recovery_params(content)
    assert params["backoff"] is True and params["drop_stale_partials"] is True
