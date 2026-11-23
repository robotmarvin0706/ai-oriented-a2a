"""Timeout-recovery experiment (experiments/timeout_recovery): reproducibility,
controls, content policy, schema. Small N except where the committed data is
regenerated (the whole run takes well under a second).

Run: pytest tests/experiments -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_oriented_a2a import scan_directives, validate  # noqa: E402
from ai_oriented_a2a.transport import send_message  # noqa: E402
from experiments.timeout_recovery import run as runner  # noqa: E402
from experiments.timeout_recovery.host import (  # noqa: E402
    MODEL_NAME,
    Case,
    Host,
    LLMStrategyModel,
    RuleStrategyModel,
    naive_policy,
)
from experiments.timeout_recovery.packets import (  # noqa: E402
    PEER_POLICY,
    control_packets,
    distilled_packet,
)
from ai_oriented_a2a.recovery import parse_recovery_params, policy_from_params  # noqa: E402

pytest.importorskip("jsonschema")

DATA = REPO / "datasets" / "timeout_recovery" / "runs"
SUMMARIES = REPO / "experiments" / "timeout_recovery" / "summaries"
SEEDS = (42, 7, 2026)
LEGAL_PREFIXES = ("synthetic:", "local_run:", "peer_absorbed:")


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- reproducibility -------------------------------------------------------


def test_same_seed_twice_is_byte_identical_across_processes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    args = ["--seeds", "42", "--n", "5", "--commit", "test"]
    out = lambda d: ["--data-dir", str(d / "data"), "--results", str(d / "RESULTS.md"), "--summary-dir", str(d / "sum")]  # noqa: E731
    runner.main(args + out(a))
    env = dict(os.environ, PYTHONHASHSEED="12345")
    subprocess.run(
        [sys.executable, "-m", "experiments.timeout_recovery.run", *args, *out(b)],
        cwd=REPO, env=env, check=True, capture_output=True,
    )
    ta, tb = _tree(a), _tree(b)
    assert sorted(ta) == ["RESULTS.md", "data/42/controls.jsonl", "data/42/trajectories.jsonl", "sum/seed-42.json"]
    assert ta == tb


def test_committed_data_matches_a_fresh_run():
    result = runner.run_all(SEEDS, 30, RuleStrategyModel)
    for seed in SEEDS:
        files = runner.seed_files(result["per_seed"][seed], MODEL_NAME)
        assert (SUMMARIES / f"seed-{seed}.json").read_text(encoding="utf-8") == files.pop("summary.json")
        for name, text in files.items():
            assert (DATA / str(seed) / name).read_text(encoding="utf-8") == text, f"{seed}/{name} is stale; rerun the experiment"


def test_results_md_tables_match_a_fresh_run():
    result = runner.run_all(SEEDS, 30, RuleStrategyModel)
    committed = (REPO / "experiments" / "timeout_recovery" / "RESULTS.md").read_text(encoding="utf-8")
    fresh = runner.results_markdown(result, SEEDS, 30, MODEL_NAME, "x")
    # Everything after the environment block is data; it must not drift.
    tail = lambda text: text.split("## Seed 42", 1)[1]  # noqa: E731
    assert tail(committed) == tail(fresh)


# --- controls: content has to matter ---------------------------------------


def test_wrong_packet_on_naive_host_gains_nothing_and_rolls_back():
    for i in range(1, 6):
        host = Host(Case(42, i))
        before = host.policy
        reply = host.handle_message(control_packets(42)["wrong_packet"])
        rec = host.log[-1]
        assert rec.params == {"backoff": False, "max_attempts": None, "drop_stale_partials": False}
        assert rec.isolation_dsr <= 0
        assert reply["status"] == "rejected" and rec.rollback_triggered and rec.rollback_ok
        assert reply["effect_summary"]["metrics"]["rollback_triggered"] is True
        assert host.policy == before and host.prompt_blocks == []


def test_wrong_packet_on_working_host_has_negative_delta_and_rolls_back():
    negatives = 0
    for i in range(1, 11):
        host = Host(Case(42, i))
        if host.handle_message(distilled_packet(42))["status"] != "absorbed":
            continue
        good = host.policy
        blocks = list(host.prompt_blocks)
        reply = host.handle_message(control_packets(42)["wrong_packet"])
        rec = host.log[-1]
        assert rec.isolation_dsr <= 0
        negatives += rec.isolation_dsr < 0
        assert reply["status"] == "rejected" and rec.rollback_triggered and rec.rollback_ok
        assert host.policy == good and host.prompt_blocks == blocks
    assert negatives >= 3  # bad advice measurably hurts a receiver that already recovers


@pytest.mark.parametrize("name", ["empty_packet", "irrelevant_packet"])
def test_empty_or_unrelated_packet_leaves_policy_unchanged(name):
    host = Host(Case(7, 1))
    reply = host.handle_message(control_packets(7)[name])
    assert reply["status"] == "rejected"
    assert "no recovery parameters" in reply["reason"]
    metrics = reply["effect_summary"]["metrics"]
    assert metrics["strategy_parsed"] is False and metrics["rollback_triggered"] is False
    assert host.policy == naive_policy() and host.prompt_blocks == []
    validate(reply, "absorption-result")


def test_injection_packet_is_rejected_before_the_model_sees_it():
    model = RuleStrategyModel()
    host = Host(Case(2026, 1), model)
    packet = control_packets(2026)["injection_packet"]
    assert scan_directives(packet["content"])  # the library matcher flags it
    reply = host.handle_message(packet)
    assert reply["status"] == "rejected" and reply["reason"].startswith("content_policy:")
    assert model.calls == 0
    assert host.policy == naive_policy() and host.prompt_blocks == [] and host.retrieval_store == {}


def test_strategy_comes_from_content_not_from_hint():
    packet = dict(distilled_packet(42), content={"trigger": "tool_call_timeout"})
    packet["absorption"] = dict(packet["absorption"], hint="apply exponential backoff from 400 ms, 4 attempts")
    host = Host(Case(42, 1))
    reply = host.handle_message(packet)
    assert reply["status"] == "rejected" and host.policy == naive_policy()


def test_distilled_content_sets_the_stated_policy():
    params, _ = parse_recovery_params(distilled_packet(42)["content"])
    got = policy_from_params(naive_policy(), params)
    assert (got.backoff, got.base_backoff_ms, got.max_attempts, got.drop_stale_partials) == (
        PEER_POLICY.backoff, PEER_POLICY.base_backoff_ms, PEER_POLICY.max_attempts, PEER_POLICY.drop_stale_partials,
    )


def test_seed_controls_and_judgement_are_reported_not_tuned():
    result = runner.run_all((42,), 30, RuleStrategyModel)
    r = result["per_seed"][42]
    assert all(r["controls"].values())
    # The verdict is whatever the fixed rule says, under each token view.
    assert r["judgement"] == runner.judge_seed(runner.token_view(r["arms"], "A"))
    assert r["judgement_params_only"] == runner.judge_seed(runner.token_view(r["arms"], "B"))
    # Thresholds are the pre-registered ones (WP1 must not move them).
    assert (runner.DSR_THRESHOLD, runner.EXTRA_COST_CAP, runner.ISOLATION_N) == (0.05, 1.5, 3)
    assert (runner.TOTAL_TOKEN_BUDGET, runner.ABSORB_TOKEN_BUDGET) == (3000, 800)


# --- schema and data policy --------------------------------------------------


def _lines(seed: int, name: str):
    path = DATA / str(seed) / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("seed", SEEDS)
def test_run_packets_validate_and_carry_verification_claim(seed):
    packets = _lines(seed, "trajectories.jsonl") + _lines(seed, "controls.jsonl")
    n_arms = len(runner.ARMS + runner.EXTRA_ARMS + runner.CONTROLS + runner.WORKING_CONTROLS)
    assert n_arms == 10
    assert len(packets) == n_arms * 30
    ids = set()
    for pkt in packets:
        validate(pkt, "experience-packet")
        assert pkt["id"] not in ids
        ids.add(pkt["id"])
        assert pkt["provenance"]["source_ref"].startswith(LEGAL_PREFIXES)
        claim = pkt["verification_claim"]
        for key in ("expected_effect", "replay_hint", "scenario_ref", "metric"):
            assert str(claim[key]).strip()
        assert isinstance(claim["value"], (int, float))
        assert claim["repro"] == {"seed": seed, "model": MODEL_NAME, "n": 30}
        assert scan_directives(pkt["content"]) == []
    arms = {p["content"]["arm"] for p in packets}
    assert arms == set(runner.ARMS) | set(runner.EXTRA_ARMS) | set(runner.CONTROLS) | set(runner.WORKING_CONTROLS)


def test_absorption_results_validate():
    for name, packet in {"distilled": distilled_packet(42), **control_packets(42)}.items():
        reply = Host(Case(42, 3)).handle_message(packet)
        validate(reply, "absorption-result")


def test_new_data_stays_under_two_megabytes():
    total = sum(p.stat().st_size for p in DATA.rglob("*") if p.is_file())
    assert total < 2 * 1024 * 1024


# --- transport and optional LLM adapter ------------------------------------


def test_http_handler_gives_the_same_reply_as_in_process():
    expected = Host(Case(42, 2)).handle_message(distilled_packet(42))
    server = Host(Case(42, 2)).serve()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        assert send_message(url, distilled_packet(42), timeout=10) == expected
    finally:
        server.shutdown()
        server.server_close()


def test_llm_adapter_is_optional_and_skipped_without_config():
    assert LLMStrategyModel(env={}).available() is False
    with pytest.raises(SystemExit):
        runner.build_model_factory("llm") if not LLMStrategyModel().available() else pytest.skip("LLM configured")
