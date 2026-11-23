"""Information-only tables added for the PR #53 review: amortization
sensitivity of the extra-token ratio, and the policies own_history's search
picks. Neither enters judge_seed / judge_overall."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.timeout_recovery import run as runner  # noqa: E402
from experiments.timeout_recovery.host import (  # noqa: E402
    SEARCH_BASE_BACKOFF_MS,
    SEARCH_DROP_STALE,
    SEARCH_MAX_ATTEMPTS,
    Policy,
    RuleStrategyModel,
)

DATA = REPO / "datasets" / "timeout_recovery" / "runs"
SUMMARIES = REPO / "experiments" / "timeout_recovery" / "summaries"
SEEDS = (42, 7, 2026)


@pytest.fixture(scope="module")
def seed42():
    return runner.run_all((42,), 30, RuleStrategyModel)["per_seed"][42]


def test_one_time_plus_eval_extra_is_extra(seed42):
    for name, a in seed42["arms"].items():
        for sfx in ("", "_b"):
            assert abs(a[f"one_time_tokens{sfx}"] + a[f"eval_extra_tokens{sfx}"] - a[f"extra_tokens{sfx}"]) < 1e-3, name


@pytest.mark.parametrize("seed", SEEDS)
def test_amortization_matches_the_committed_trajectories(seed):
    """Recompute the split from the shipped JSON Lines, independently of run.py."""
    rows: dict = {}
    for line in (DATA / str(seed) / "trajectories.jsonl").read_text(encoding="utf-8").splitlines():
        c = json.loads(line)["content"]
        rows.setdefault(c["arm"], {})[c["case_id"]] = c
    n = len(rows["no_sharing"])
    summary = json.loads((SUMMARIES / f"seed-{seed}.json").read_text(encoding="utf-8"))["amortization"]
    amort = {}
    for view, ek, ok in (("A", "tokens", "overhead_tokens"), ("B", "tokens_params_only", "overhead_tokens_params_only")):
        for arm in runner.AMORTIZE_ARMS:
            one = sum(rows[arm][k][ok] for k in rows[arm]) / n
            ev = sum(sum(e[ek] for e in rows[arm][k]["episodes"]) - sum(e[ek] for e in rows["no_sharing"][k]["episodes"])
                     for k in rows[arm]) / n
            amort[(view, arm)] = one / n + ev
            got = summary[view][arm]
            assert abs(got["one_time"] - one) < 1e-3 and abs(got["eval_extra"] - ev) < 1e-3
        for arm in runner.AMORTIZE_ARMS:
            ratio = amort[(view, arm)] / amort[(view, "own_history")]
            assert abs(summary[view][arm]["ratio_vs_own_history"] - ratio) < 1e-3


def test_amortization_does_not_enter_the_verdict(seed42):
    assert seed42["judgement"] == runner.judge_seed(runner.token_view(seed42["arms"], "A"))
    assert seed42["judgement_params_only"] == runner.judge_seed(runner.token_view(seed42["arms"], "B"))
    stripped = {k: v for k, v in seed42.items() if k not in ("amortization", "own_history_picks")}
    assert runner.judge_overall({42: stripped}) == runner.judge_overall({42: seed42})


def test_own_history_picks_account_for_every_case(seed42):
    p = seed42["own_history_picks"]
    assert sum(p["picked"].values()) == sum(p["final_after_gate"].values()) == 30
    assert p["picked_peer_policy"] == p["picked"].get(runner.PEER_POLICY.label(), 0)
    assert sum(p["peer_vs_pick_on_explore"].values()) == 30
    assert p["peer_vs_pick_on_explore"]["picked_peer"] == p["picked_peer_policy"]


def test_label_round_trip_over_the_search_space():
    labels = []
    for d in SEARCH_DROP_STALE:
        for n in SEARCH_MAX_ATTEMPTS:
            labels.append(Policy(backoff=False, max_attempts=n, drop_stale_partials=d))
            for ms in SEARCH_BASE_BACKOFF_MS:
                labels.append(Policy(backoff=True, base_backoff_ms=ms, max_attempts=n, drop_stale_partials=d))
    for pol in labels:
        got = runner.policy_from_params(runner.naive_policy(), runner._params_from_label(pol.label()))
        assert got.label() == pol.label()


def test_results_md_has_the_information_tables():
    text = (REPO / "experiments" / "timeout_recovery" / "RESULTS.md").read_text(encoding="utf-8")
    assert "## Amortization sensitivity (information only, not part of the verdict)" in text
    assert "## own_history search picks (information only)" in text
    assert "The verdict is sensitive to amortization" in text
