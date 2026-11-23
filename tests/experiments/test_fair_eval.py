"""WP1 fair comparison: own_history searches the packet's parameter space, a
mediocre peer arm, the wrong packet on a naive vs a working host, two token
views, a legal reply instead of a bare ack, and examples/ not importing
experiments/."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_oriented_a2a.recovery import parse_recovery_params, policy_from_params  # noqa: E402
from experiments.timeout_recovery import run as runner  # noqa: E402
from experiments.timeout_recovery.host import (  # noqa: E402
    CALL_TOKENS,
    OWN_SEARCH_TOKEN_BUDGET,
    SEARCH_BASE_BACKOFF_MS,
    SEARCH_DROP_STALE,
    SEARCH_MAX_ATTEMPTS,
    WINDOW_EPISODES,
    Case,
    Host,
    RuleStrategyModel,
    naive_policy,
    run_window,
    search_own_policy,
    sr,
)
from experiments.timeout_recovery.packets import (  # noqa: E402
    MEDIOCRE_POLICY,
    PEER_POLICY,
    control_packets,
    mediocre_packet,
    working_policy,
)


@pytest.fixture(scope="module")
def seed42():
    return runner.run_all((42,), 30, RuleStrategyModel)["per_seed"][42]


def _eval_sr(policy, case):
    return sr(run_window(policy, case, "immediate") + run_window(policy, case, "delayed"))


# --- WP1-1 own_history searches the same space -------------------------------


def test_search_space_contains_the_peer_policy():
    assert PEER_POLICY.base_backoff_ms in SEARCH_BASE_BACKOFF_MS
    assert PEER_POLICY.max_attempts in SEARCH_MAX_ATTEMPTS
    assert PEER_POLICY.drop_stale_partials in SEARCH_DROP_STALE
    assert MEDIOCRE_POLICY.max_attempts in SEARCH_MAX_ATTEMPTS


def test_search_is_deterministic_and_stays_in_the_space():
    labels = set()
    for i in range(1, 16):
        a, b = search_own_policy(Case(42, i)), search_own_policy(Case(42, i))
        assert a.params == b.params and [e.compact() for e in a.episodes] == [e.compact() for e in b.episodes]
        assert set(a.params) <= {"backoff", "base_backoff_ms", "max_attempts", "drop_stale_partials"}
        if "base_backoff_ms" in a.params:
            assert a.params["base_backoff_ms"] in SEARCH_BASE_BACKOFF_MS and a.params["backoff"] is True
        if "max_attempts" in a.params:
            assert a.params["max_attempts"] in SEARCH_MAX_ATTEMPTS
        assert a.best_successes >= a.start_successes  # never picks worse than the start on its own draws
        labels.add(policy_from_params(naive_policy(), a.params).label())
    # Not a fixed rule any more: different cases end up with different policies,
    # and not only the old fixed 200 ms / 3 attempts.
    assert len(labels) >= 3
    assert any("exp200ms,max_attempts=3" not in lab for lab in labels)


def test_search_respects_its_token_budget():
    for i in range(1, 31):
        res = search_own_policy(Case(7, i))
        spent = sum(e.tool_calls * CALL_TOKENS for e in res.episodes)
        per_candidate = WINDOW_EPISODES["explore"]
        last = sum(e.tool_calls * CALL_TOKENS for e in res.episodes[-per_candidate:])
        assert spent - last < OWN_SEARCH_TOKEN_BUDGET  # stops before a candidate once the budget is spent
        assert len(res.episodes) == res.candidates * per_candidate
    tiny = search_own_policy(Case(7, 1), budget=1)
    assert tiny.candidates == 1 and tiny.budget_stopped and tiny.params == {}


def test_own_history_arm_pays_for_its_search_and_uses_the_gate(seed42):
    oh = seed42["arms"]["own_history"]
    assert oh["extra_tokens"] == oh["extra_tokens_b"]  # carries only parameters in both views
    assert oh["extra_tool_calls"] > 30  # search tool calls are counted
    assert oh["candidates"] > 0 and oh["rollbacks"] + oh["accepted"] == oh["candidates"]


# --- WP1-2 mediocre peer -------------------------------------------------------


def test_mediocre_peer_is_only_slightly_better_than_naive_on_the_calibration_seed():
    gains = [_eval_sr(MEDIOCRE_POLICY, Case(1, i)) - _eval_sr(naive_policy(), Case(1, i)) for i in range(1, 31)]
    mean = sum(gains) / len(gains)
    assert 0 < mean < 0.1
    peer = sum(_eval_sr(PEER_POLICY, Case(1, i)) - _eval_sr(naive_policy(), Case(1, i)) for i in range(1, 31)) / 30
    assert peer > 4 * mean


def test_mediocre_packet_states_the_mediocre_policy():
    params, _ = parse_recovery_params(mediocre_packet(7)["content"])
    got = policy_from_params(naive_policy(), params)
    assert got.label() == MEDIOCRE_POLICY.label()


def test_mediocre_arm_is_reported_and_judged_for_information(seed42):
    assert "mediocre_packet" in seed42["arms"]
    mj = seed42["mediocre_judgement"]
    assert set(mj) == {"A", "B"}
    for view in ("A", "B"):
        assert mj[view] == runner.judge_seed(runner.token_view(seed42["arms"], view, "mediocre_packet"))


# --- WP1-3 wrong packet on naive vs working host ------------------------------


def test_wrong_packet_separates_no_effect_from_harm(seed42):
    w = seed42["wrong_by_host"]
    naive, working = w["naive_host"], w["working_host"]
    assert naive["host_policy"] == naive_policy().label()
    assert working["host_policy"] == working_policy().label()
    assert working["host_sr"] > naive["host_sr"]
    assert naive["ungated_effect"] != "harmful"
    assert working["ungated_effect"] == "harmful" and working["ungated_delta_sr_ci95"][1] < 0
    assert working["isolation_dsr_negative_cases"] > 0
    for row in (naive, working):  # the gate keeps neither
        assert row["accepted"] == 0 and row["rollbacks"] == row["n"] and row["rollback_success_rate"] == 1.0
        assert row["gated_delta_sr"] == 0 and row["policy_unchanged"] == row["n"]


def test_working_host_wrong_packet_rolls_back_to_the_working_policy():
    host = Host(Case(42, 4))
    host.policy = working_policy()
    reply = host.handle_message(control_packets(42)["wrong_packet"])
    assert reply["status"] == "rejected" and host.policy == working_policy()
    assert host.log[-1].rollback_ok is True


# --- WP1-4 two token views -----------------------------------------------------


def test_token_views_on_packet_arms(seed42):
    arms = seed42["arms"]
    for name in ("distilled_packet", "raw_trajectory", "mediocre_packet"):
        assert arms[name]["extra_tokens_b"] < arms[name]["extra_tokens"]
    assert arms["no_sharing"]["extra_tokens"] == arms["no_sharing"]["extra_tokens_b"] == 0
    for case in seed42["per_case"]:
        for name, row in case.items():
            assert row.tokens_b <= row.tokens, name


def test_both_views_are_judged_with_the_same_rule(seed42):
    for view, key in (("A", "judgement"), ("B", "judgement_params_only")):
        assert seed42[key] == runner.judge_seed(runner.token_view(seed42["arms"], view))
    assert runner.token_view(seed42["arms"], "B")["distilled_packet"]["extra_tokens"] == seed42["arms"]["distilled_packet"]["extra_tokens_b"]


def test_results_md_reports_both_views():
    text = (REPO / "experiments" / "timeout_recovery" / "RESULTS.md").read_text(encoding="utf-8")
    for needle in (
        "Overall verdict (fixed rule), token view A:",
        "Overall verdict (fixed rule), token view B:",
        "Decision checks, token view B:",
        "| extra tokens A | extra tokens B |",
        "## mediocre_packet vs own_history (information only)",
        "Wrong packet by host",
    ):
        assert needle in text


# --- WP1-5 no bare ack -----------------------------------------------------------


@pytest.mark.parametrize(
    "msg, prefix",
    [
        ({"version": "0.4", "agent_id": "a", "constraints": ["x"]}, "unsupported_message:"),
        ({"version": "0.4", "packet_ref": "pkt-1", "status": "absorbed", "effect_summary": {"note": "ok"}}, "unsupported_message:"),
        ({"version": "0.4", "id": "pkt-x", "type": "other", "content": {}}, "schema_invalid:"),
        ({"version": "0.4", "hello": "world"}, "schema_invalid:"),
    ],
)
def test_non_packet_messages_get_a_rejected_absorption_result(msg, prefix):
    pytest.importorskip("jsonschema")
    from ai_oriented_a2a import validate

    host = Host(Case(42, 1))
    reply = host.handle_message(msg)
    assert "ack" not in reply
    assert reply["status"] == "rejected" and reply["reason"].startswith(prefix)
    validate(reply, "absorption-result")
    assert host.policy == naive_policy() and host.log == []


# --- WP1-6 examples/ does not depend on experiments/ ----------------------------


def test_examples_do_not_import_experiments():
    for path in sorted((REPO / "examples").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("experiments"), f"{path.name} imports {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("experiments"), f"{path.name} imports {alias.name}"


def test_minimal_example_runs_without_loading_experiments():
    code = (
        "import sys; sys.path.insert(0, 'examples'); import minimal_dual_end; "
        "print(any(m == 'experiments' or m.startswith('experiments.') for m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_shared_helpers_live_in_the_main_package():
    import experiments.timeout_recovery.host as host_mod
    sys.path.insert(0, str(REPO / "examples"))
    import minimal_dual_end

    assert host_mod.parse_recovery_params is parse_recovery_params
    assert host_mod.policy_from_params is policy_from_params
    assert minimal_dual_end.parse_recovery_params is parse_recovery_params
    assert minimal_dual_end.policy_from_params is policy_from_params
    assert not (REPO / "experiments" / "timeout_recovery" / "strategy.py").exists()


def test_policy_from_params_is_generic_and_pure():
    @dataclass
    class Small:  # no base_backoff_ms, like the example's Policy
        backoff: bool = False
        max_attempts: int = 3
        drop_stale_partials: bool = False

    cur = Small()
    new = policy_from_params(cur, {"backoff": True, "base_backoff_ms": 400, "max_attempts": None, "drop_stale_partials": True},
                             unbounded_attempts=50)
    assert cur == Small() and new == Small(True, 50, True) and not hasattr(new, "base_backoff_ms")
    got = policy_from_params(naive_policy(), json.loads('{"max_attempts": null, "base_backoff_ms": 400}'))
    assert got.max_attempts is None and got.base_backoff_ms == 400
