"""WP3 runner (``wp3.py``): summary shape, determinism, and the fixed rules.

Tiny runs (N=2) so these stay fast; the reported run is four seeds at N=30.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.timeout_recovery import run as runner  # noqa: E402
from experiments.timeout_recovery import wp3  # noqa: E402
from experiments.timeout_recovery.host import RuleStrategyModel  # noqa: E402

N = 2
SEED = 42
ARMS = ("own_history", "plain_file", "plain_file_gated", "protocol", "retrieval_bridge")
ROW_KEYS = {
    "n", "sr", "delta_sr", "delta_sr_ci95", "extra_tokens", "extra_tokens_b",
    "amortized_extra_tokens", "amortized_extra_tokens_b", "accepted",
    "content_policy_rejections", "attributable",
}


def _summary(seed: int = SEED, n: int = N) -> dict:
    return wp3.run_seed(seed, n, RuleStrategyModel)


def test_summary_shape():
    summary = _summary()
    assert set(summary) == {"seed", "n", "hosts", "paired"}
    assert set(summary["hosts"]) == set(runner.WP3_HOSTS)
    for host, arms in summary["hosts"].items():
        assert set(arms) == set(ARMS), (host, set(arms))
        assert set(arms["own_history"]) == {"none"}, "own_history takes no packet"
        for arm in ARMS[1:]:
            assert set(arms[arm]) == set(runner.WP3_PACKETS), (host, arm)
        for row in arms["protocol"].values():
            assert ROW_KEYS <= set(row), ROW_KEYS - set(row)
            assert row["n"] == N
            assert len(row["delta_sr_ci95"]) == 2


def test_paired_ci_is_over_cases():
    summary = _summary()
    for host in runner.WP3_HOSTS:
        paired = summary["paired"][host]["protocol_minus_plain_file_gated"]["distilled_packet"]
        assert isinstance(paired["delta_sr"], float)
        lo, hi = paired["ci95"]
        assert lo <= paired["delta_sr"] <= hi


def test_run_is_deterministic():
    assert json.dumps(_summary(), sort_keys=True, default=str) == json.dumps(_summary(), sort_keys=True, default=str)


def test_only_the_protocol_arm_is_attributable():
    summary = _summary()
    proto = summary["hosts"]["naive_host"]["protocol"]["distilled_packet"]
    gated = summary["hosts"]["naive_host"]["plain_file_gated"]["distilled_packet"]
    assert proto["attributable"] > 0
    assert gated["attributable"] == 0
    # The two arms reach the same decisions; only attribution differs.
    assert proto["accepted"] == gated["accepted"]
    assert proto["delta_sr"] == gated["delta_sr"]


def test_gated_arms_reject_injection_by_content_policy():
    summary = _summary()
    for host in runner.WP3_HOSTS:
        for arm in ("plain_file_gated", "protocol", "retrieval_bridge"):
            row = summary["hosts"][host][arm]["injection_packet"]
            assert row["accepted"] == 0, (host, arm)
            assert row["content_policy_rejections"] == N, (host, arm)


def test_plain_file_is_ungated_by_design():
    summary = _summary()
    row = summary["hosts"]["naive_host"]["plain_file"]["injection_packet"]
    assert row["content_policy_rejections"] == 0  # R4 excludes this arm for this reason
    assert row["accepted"] == 0


def test_retrieval_rows_carry_retrieval_stats():
    summary = _summary()
    row = summary["hosts"]["naive_host"]["retrieval_bridge"]["distilled_packet"]
    assert row["retrieval_hits"] == N
    assert row["retrieved_items"] == N
    assert row["retrieved_tokens"] > 0


def test_own_history_row_has_no_packet_and_keeps_its_search_cost():
    summary = _summary()
    row = summary["hosts"]["naive_host"]["own_history"]["none"]
    assert row["one_time_tokens"] > 0  # the search is charged to own_history
    assert row["accepted"] + 0 >= 0


def test_rules_integrate_on_a_real_run():
    result = wp3.run_all([SEED, 7], N, RuleStrategyModel)
    overall = result["overall"]
    assert overall["result"] in ("reported", "FAIL")
    assert overall["protocol_increment"] in ("none", "increment", "undetermined")
    assert isinstance(overall["gate_has_value"], bool)
    for seed in (SEED, 7):
        judged = overall["per_seed"][seed]
        assert set(judged) >= {"R1_gate_value", "R2_protocol_increment", "R3_retrieval_feasible", "R4_safety"}


def test_markdown_reports_verdicts():
    result = wp3.run_all([SEED], N, RuleStrategyModel)
    text = wp3.results_markdown(result, [SEED], N, "deadbee")
    assert "## Verdicts" in text and "R2 protocol increment" in text
    assert "protocol_increment" in text and "safety_pass" in text
    assert "deadbee" in text
