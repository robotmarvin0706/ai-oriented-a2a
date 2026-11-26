"""Tests of experiments/expiry/analysis.py (PREREG_EXPIRY §8-§12). Fast, synthetic."""

from __future__ import annotations

import json

import pytest

from experiments.expiry import analysis as an
from experiments.expiry.run import results_markdown
from experiments.expiry.sim import TASKS, simulate_run, run_endpoints

BASE_EP = {
    "recovery_window_sr": 0.5,
    "pre_shift_sr": 0.9,
    "pre_ref_sr": 0.9,
    "full_sequence_sr": 0.6,
    "tasks_to_recover": 10,
    "tasks_to_recover_censored": False,
    "stale_policy_uses_window": 4,
    "stale_policy_uses_total": 8,
    "extra_tool_calls": 0,
    "false_positive_expiries": 0,
    "expiry_events": 3,
    "total_tokens": 10000,
    "expiry_overhead_tokens": 100,
    "policy_changes": 4,
    "attributed_changes": 4,
}


def mk_run(seed, run=0, silent=False, bump=False, **arms):
    """One synthetic run_endpoints-like dict; arm values override BASE_EP."""
    out = {}
    for label, over in arms.items():
        ep = dict(BASE_EP)
        ep.update(over)
        ep["arm"] = label
        out[label] = ep
    return {"seed": seed, "run": run, "silent": silent, "bump": bump,
            "detected": not silent, "arms": out}


def runs_from(per_seed):
    """Build one run per seed from {seed: {arm: {field: value}}}."""
    return [mk_run(seed, run=0, **arms) for seed, arms in per_seed.items()]


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_is_deterministic_for_a_contrast_name():
    strata = {42: [0.1, -0.2, 0.3, 0.0], 7: [0.2, 0.05, -0.1, 0.4]}
    first = an.bootstrap_ci(strata, "D-C:recovery_window_SR")
    second = an.bootstrap_ci(strata, "D-C:recovery_window_SR")
    assert first == second
    point, lo, hi = first
    assert lo < point < hi
    assert point == pytest.approx((0.1 - 0.2 + 0.3 + 0.0 + 0.2 + 0.05 - 0.1 + 0.4) / 8)


def test_bootstrap_seed_depends_on_contrast_name_only():
    strata = {42: [0.1, -0.2, 0.3, 0.0], 7: [0.2, 0.05, -0.1, 0.4]}
    a = an.bootstrap_ci(strata, "one")
    b = an.bootstrap_ci(strata, "one")
    c = an.bootstrap_ci(dict(reversed(list(strata.items()))), "one")
    assert a == b == c  # seed ordering does not change the draw


def test_bootstrap_resamples_within_seeds():
    # Constant values per seed: a stratified bootstrap cannot move the estimate.
    strata = {42: [0.0] * 5, 7: [1.0] * 5}
    point, lo, hi = an.bootstrap_ci(strata, "stratified")
    assert point == pytest.approx(0.5)
    assert lo == pytest.approx(0.5) and hi == pytest.approx(0.5)


def test_bootstrap_empty_input_is_zero():
    assert an.bootstrap_ci({}, "empty") == (0.0, 0.0, 0.0)


def test_contrast_reports_per_seed_means_and_half_width():
    runs = runs_from({
        42: {"D": {"recovery_window_sr": 0.6}, "C": {"recovery_window_sr": 0.5}},
        7: {"D": {"recovery_window_sr": 0.7}, "C": {"recovery_window_sr": 0.5}},
    })
    c = an.contrast(runs, "D", "C", "recovery_window_sr", "D-C:recovery_window_SR")
    assert c["estimate"] == pytest.approx(0.15)
    assert c["per_seed"] == {"7": pytest.approx(0.2), "42": pytest.approx(0.1)}
    assert c["half_width"] == pytest.approx((c["hi"] - c["lo"]) / 2)
    assert c["runs"] == 2


# ---------------------------------------------------------------------------
# V1 and V2 (§11)
# ---------------------------------------------------------------------------


def test_v1_passes_when_content_reaches_sr():
    runs = [mk_run(seed, C={"pre_ref_sr": 0.9}, V1={"pre_ref_sr": 0.3}) for seed in (42, 7)]
    v1 = an.v1_check(runs)
    assert v1["outcome"] == "pass"
    assert v1["estimate"] == pytest.approx(0.6)


def test_v1_fails_when_ci_lower_bound_is_not_positive():
    runs = [mk_run(seed, C={"pre_ref_sr": 0.5}, V1={"pre_ref_sr": 0.5}) for seed in (42, 7)]
    v1 = an.v1_check(runs)
    assert v1["outcome"] == "fail"
    assert "V1 failed" in v1["reason"]


def test_v2_pass_when_ci_inside_the_band():
    runs = [mk_run(seed, V2={"recovery_window_sr": 0.52}, C={"recovery_window_sr": 0.50})
            for seed in (42, 7)]
    assert an.v2_check(runs)["outcome"] == "pass"


def test_v2_void_when_random_expiry_would_meet_r1a():
    runs = [mk_run(seed, V2={"recovery_window_sr": 0.60}, C={"recovery_window_sr": 0.50})
            for seed in (42, 7)]
    v2 = an.v2_check(runs)
    assert v2["outcome"] == "VOID"
    assert "VOID" in v2["reason"]


def test_v2_inconclusive_when_ci_reaches_beyond_the_band():
    runs = []
    for seed in (42, 7):
        for i in range(6):
            shift = -0.4 if i % 2 else 0.4
            runs.append(mk_run(seed, run=i, V2={"recovery_window_sr": 0.5 + shift},
                               C={"recovery_window_sr": 0.5}))
    v2 = an.v2_check(runs)
    assert v2["outcome"] == "inconclusive"
    assert v2["lo"] < -an.DELTA_F and v2["hi"] > an.DELTA_F
    assert "capped at undetermined" in v2["reason"]


# ---------------------------------------------------------------------------
# Decision rules and labels (§12)
# ---------------------------------------------------------------------------


def prim(est, lo, hi):
    return {"estimate": est, "lo": lo, "hi": hi, "half_width": (hi - lo) / 2}


def test_rule_labels_are_exact():
    assert an.LABEL_VOID == "VOID"
    assert an.LABEL_R1A == ("supported: enforcing machine-checkable expiry improves "
                           "recovery-window SR by at least the margin")
    assert an.LABEL_R1B == "positive but below the 0.08 margin; not practically significant"
    assert an.LABEL_R3 == "enforcing expiry is worse"
    assert an.LABEL_R2 == "equivalent within ±0.08 recovery-window SR"
    assert an.LABEL_R4 == "undetermined"


@pytest.mark.parametrize("est, lo, hi, rule, label", [
    (0.10, 0.05, 0.15, "R1a", an.LABEL_R1A),
    (0.08, 0.01, 0.15, "R1a", an.LABEL_R1A),
    (0.05, 0.01, 0.09, "R1b", an.LABEL_R1B),
    (-0.05, -0.10, -0.01, "R3", an.LABEL_R3),
    (0.00, -0.05, 0.05, "R2", an.LABEL_R2),
    (0.05, -0.05, 0.15, "R4", an.LABEL_R4),
])
def test_decide_matches_each_rule(est, lo, hi, rule, label):
    d = an.decide(prim(est, lo, hi), "pass", "pass")
    assert (d["rule"], d["label"], d["headline"]) == (rule, label, label)
    assert d["half_width"] == pytest.approx((hi - lo) / 2)
    assert d["saturation"] is False and d["v1"] == "pass" and d["v2"] == "pass"


@pytest.mark.parametrize("v1, v2", [("fail", "pass"), ("pass", "VOID")])
def test_v_row_is_void_with_a_reason(v1, v2):
    d = an.decide(prim(0.10, 0.05, 0.15), v1, v2)
    assert (d["rule"], d["label"], d["headline"]) == ("V", an.LABEL_VOID, an.LABEL_VOID)
    assert d["reason"]
    # the matched R rule is still reported as information
    assert d["information_rule"] == "R1a" and d["information_label"] == an.LABEL_R1A


def test_v2_inconclusive_caps_headline_at_undetermined():
    d = an.decide(prim(0.10, 0.05, 0.15), "pass", "inconclusive")
    assert (d["rule"], d["label"]) == ("R1a", an.LABEL_R1A)
    assert d["headline"] == an.LABEL_R4
    assert "capped at undetermined" in d["reason"]


def test_secondary_role_never_has_a_headline():
    d = an.decide(prim(0.10, 0.05, 0.15), None, None, role="secondary")
    assert d["headline"] is None
    assert d["label"] == an.LABEL_R1A


def test_half_width_flag_at_0034():
    d = an.decide(prim(0.0, -0.034, 0.034), "pass", "pass")
    assert d["half_width"] == pytest.approx(0.034)
    assert d["noisier_than_planned"] is False
    d2 = an.decide(prim(0.0, -0.0341, 0.0341), "pass", "pass")
    assert d2["noisier_than_planned"] is True


def test_saturation_flag_outside_the_planned_band():
    ok = [mk_run(seed, C={"recovery_window_sr": 0.384}, D={"recovery_window_sr": 0.535})
          for seed in (42, 7)]
    sat = an.saturation_check(ok)
    assert sat["saturated"] is False and sat["arms_outside"] == []

    low = [mk_run(42, C={"recovery_window_sr": 0.05}, D={"recovery_window_sr": 0.5})]
    high = [mk_run(42, C={"recovery_window_sr": 0.5}, D={"recovery_window_sr": 0.95})]
    assert an.saturation_check(low)["arms_outside"] == ["C"]
    assert an.saturation_check(high)["arms_outside"] == ["D"]
    d = an.decide(prim(0.0, -0.02, 0.02), "pass", "pass", an.saturation_check(high))
    assert d["saturation"] is True


# ---------------------------------------------------------------------------
# tasks_to_recover censoring (§8.2)
# ---------------------------------------------------------------------------


def test_tasks_to_recover_censoring_aggregation():
    runs = [
        mk_run(42, C={"tasks_to_recover": 10},
               D={"tasks_to_recover": 5}),
        mk_run(42, C={"tasks_to_recover": 75, "tasks_to_recover_censored": True},
               D={"tasks_to_recover": 22}),
    ]
    table = an.arm_table(runs)
    assert table["C"]["tasks_to_recover_mean"] == pytest.approx(42.5)
    assert table["C"]["tasks_to_recover_median"] == pytest.approx(42.5)
    assert table["C"]["tasks_to_recover_censored"] == 1
    assert table["D"]["tasks_to_recover_mean"] == pytest.approx(13.5)
    assert table["D"]["tasks_to_recover_censored"] == 0
    assert table["C"]["attribution_rate"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Full analysis on real simulated runs
# ---------------------------------------------------------------------------


def test_analyse_on_two_simulated_runs_is_json_serialisable():
    runs = [run_endpoints(simulate_run(42, run=i)) for i in (0, 1)]
    out = an.analyse(runs)
    assert json.loads(json.dumps(out)) == out
    for key in ("n_runs", "seeds", "primary", "v1", "v2", "decision", "secondary",
                "strata", "per_seed", "arms", "observed", "saturation",
                "tasks_to_recover", "half_width", "noisier_than_planned"):
        assert key in out
    assert out["n_runs"] == 2
    assert out["seeds"] == {"42": 2}
    assert out["decision"]["rule"] in {"V", "R1a", "R1b", "R3", "R2", "R4"}
    assert out["decision"]["headline"] in {an.LABEL_VOID, an.LABEL_R1A, an.LABEL_R1B,
                                           an.LABEL_R3, an.LABEL_R2, an.LABEL_R4}
    assert out["primary"]["role"] == "primary"
    assert {c["role"] for c in out["secondary"]} == {"secondary"}
    assert len(out["secondary"]) == len(an.SECONDARY_CONTRASTS)
    assert set(out["strata"]["shift"]) == {"detected", "silent"}
    assert set(out["strata"]["bump"]) == {"yes", "no"}
    assert out["observed"]["silent_shift_rate"] in (0.0, 0.5, 1.0)
    assert set(out["arms"]) == {"A", "C", "D", "V1", "V2"}
    assert isinstance(out["saturation"]["saturated"], bool)
    assert "D-C" in out["tasks_to_recover"]


def test_tasks_to_recover_has_no_rule_while_sr_contrasts_do():
    """§8.2: tasks_to_recover is reported, no verdict -- the §12 table is SR-only."""
    runs = [run_endpoints(simulate_run(42, run=i)) for i in (0, 1)]
    out = an.analyse(runs)
    ttr = out["tasks_to_recover"]["D-C"]
    # the D - C estimate, CI and half-width are still reported
    assert ttr["metric"] == "tasks_to_recover"
    assert ttr["half_width"] == pytest.approx((ttr["hi"] - ttr["lo"]) / 2)
    assert ttr["rule"] == "n/a"
    assert "worse" not in ttr["label"].lower()
    assert "supported" not in ttr["label"].lower()
    assert "no verdict" in ttr["label"]
    assert "lower is better" in ttr["label"]
    assert ttr["label"] == an.LABEL_NO_VERDICT_TASKS
    # SR contrasts keep the §12 rule table
    sr_contrasts = [out["primary"]] + list(out["secondary"])
    for c in sr_contrasts:
        assert c["rule"] in {"R1a", "R1b", "R3", "R2", "R4"}, c["contrast"]
        assert c["label"] in an.RULE_LABELS.values(), c["contrast"]
    # the rendered report applies no §12 rule to the tasks_to_recover row
    text = results_markdown(out, [42], [0, 1], TASKS, "deadbeef")
    row = [ln for ln in text.splitlines() if ln.startswith("| D-C:tasks_to_recover")][0]
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[5] == "n/a"
    assert "worse" not in cells[6] and "supported" not in cells[6].lower()


def test_analyse_rejects_empty_input():
    with pytest.raises(ValueError):
        an.analyse([])
