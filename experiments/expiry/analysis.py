"""Analysis and decision rules of the expiry experiment (PREREG_EXPIRY §8-§12).

The unit of analysis is the run. Contrasts are paired per run (all arms of a run
share the timeline, the feed and the task draws); CIs are a percentile bootstrap
over runs, stratified by seed (runs resampled within each seed), B = 2000, 95%,
seeded with ``stable_int("expiry-bootstrap", contrast)``. The primary contrast is
D - C on ``recovery_window_SR``; every SR secondary contrast is reported with the
same rule table but labelled secondary and never carries a headline. The
``tasks_to_recover`` contrast of §8.2 is reported with its estimate, CI and
half-width but no verdict (lower is better in task counts, so the §12 table does
not apply).

Nothing here is normative: the result is one simulated tool under one simulated
regime shift, not a measurement of a real system.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from experiments.timeout_recovery.host import stable_int
from experiments.timeout_recovery.run import BOOTSTRAP_B

from .sim import ARM_ORDER, DELTA, HALF_WIDTH_FLAG, TASKS_TO_RECOVER_CENSOR

#: δ = 0.08 (§10), as a float for comparison with bootstrap estimates.
DELTA_F = float(DELTA)

#: Verdict labels, verbatim from §12.
LABEL_VOID = "VOID"
LABEL_R1A = "supported: enforcing machine-checkable expiry improves recovery-window SR by at least the margin"
LABEL_R1B = "positive but below the 0.08 margin; not practically significant"
LABEL_R3 = "enforcing expiry is worse"
LABEL_R2 = "equivalent within ±0.08 recovery-window SR"
LABEL_R4 = "undetermined"

#: §8.2 lists ``tasks_to_recover`` under "Secondary endpoints (reported, no
#: verdict)": the D - C estimate and CI are reported, but the §12 table is
#: defined on SR endpoints with margin delta = 0.08 SR and has no meaning in task
#: counts (lower is better there), so no rule is applied.
RULE_NO_VERDICT = "n/a"
LABEL_NO_VERDICT_TASKS = ("reported, no verdict (§8.2); lower is better "
                          "(fewer tasks to recover)")

RULE_LABELS: Dict[str, str] = {
    "V": LABEL_VOID,
    "R1a": LABEL_R1A,
    "R1b": LABEL_R1B,
    "R3": LABEL_R3,
    "R2": LABEL_R2,
    "R4": LABEL_R4,
}

#: §9.4: a realised arm mean outside this band is reported as saturation.
SATURATION_BAND = (0.10, 0.90)

#: Planned arm means of §9.4, for the report.
PLANNED_RECOVERY_WINDOW_SR = {"C": 0.384, "D": 0.535}
PLANNED_SILENT_SHIFT_RATE = 0.25
PLANNED_BENIGN_BUMP_RATE = 0.50


# ---------------------------------------------------------------------------
# Bootstrap (§12)
# ---------------------------------------------------------------------------


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def bootstrap_ci(diffs_by_seed: Dict[Any, Sequence[float]], contrast_name: str,
                 b: int = BOOTSTRAP_B) -> Tuple[float, float, float]:
    """Mean and 95% percentile CI of paired differences, resampling within seeds.

    Same percentile indices as ``timeout_recovery.run.bootstrap_ci``. Returns
    ``(point, lo, hi)`` as floats.
    """
    keys = sorted(diffs_by_seed)
    n = sum(len(diffs_by_seed[k]) for k in keys)
    if n == 0:
        return 0.0, 0.0, 0.0
    total = sum(sum(diffs_by_seed[k]) for k in keys)
    rng = random.Random(stable_int("expiry-bootstrap", contrast_name))
    sums: List[float] = []
    for _ in range(b):
        s = 0.0
        for k in keys:
            vals = list(diffs_by_seed[k])
            m = len(vals)
            for _ in range(m):
                s += vals[rng.randrange(m)]
        sums.append(s)
    sums.sort()
    lo = sums[int(0.025 * b)] / n
    hi = sums[int(0.975 * b) - 1] / n
    return total / n, lo, hi


def _value(run: Dict[str, Any], arm: str, metric: str) -> float:
    ep = run["arms"][arm]
    return float(ep[metric]) if metric in ep else float(run[metric])


def _strata_by_seed(runs: Sequence[Dict[str, Any]], arm_a: str, arm_b: str,
                    metric: str) -> Dict[int, List[float]]:
    strata: Dict[int, List[float]] = {}
    for r in runs:
        strata.setdefault(r["seed"], []).append(_value(r, arm_a, metric) - _value(r, arm_b, metric))
    return strata


def contrast(runs: Sequence[Dict[str, Any]], arm_a: str, arm_b: str, metric: str,
             name: str, b: int = BOOTSTRAP_B) -> Dict[str, Any]:
    """Paired contrast ``arm_a - arm_b`` on ``metric`` with its stratified CI."""
    strata = _strata_by_seed(runs, arm_a, arm_b, metric)
    est, lo, hi = bootstrap_ci(strata, name, b=b)
    return {
        "contrast": name,
        "metric": metric,
        "left": arm_a,
        "right": arm_b,
        "estimate": est,
        "lo": lo,
        "hi": hi,
        "half_width": (hi - lo) / 2,
        "per_seed": {str(s): _mean(v) for s, v in sorted(strata.items())},
        "runs": sum(len(v) for v in strata.values()),
    }


# ---------------------------------------------------------------------------
# Validity checks (§11)
# ---------------------------------------------------------------------------


def v1_check(runs: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B) -> Dict[str, Any]:
    """§11.1: pass iff the CI of C - V1 on ``pre_ref_SR`` has lower bound > 0."""
    c = contrast(runs, "C", "V1", "pre_ref_sr", "C-V1:pre_ref_SR", b=b)
    c["check"] = "V1"
    c["outcome"] = "pass" if c["lo"] > 0 else "fail"
    if c["outcome"] != "pass":
        c["reason"] = ("V1 failed: the CI of C - V1 on pre_ref_SR does not have lower bound > 0, "
                       "so absorbed content does not reach SR and the result is VOID")
    return c


def v2_check(runs: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B,
             delta: float = DELTA_F) -> Dict[str, Any]:
    """§11.2: pass / VOID / inconclusive for ``V2 - C`` on ``recovery_window_SR``."""
    c = contrast(runs, "V2", "C", "recovery_window_sr", "V2-C:recovery_window_SR", b=b)
    c["check"] = "V2"
    if c["lo"] >= -delta and c["hi"] <= delta:
        c["outcome"] = "pass"
        c["reason"] = ""
    elif c["lo"] > 0 and c["estimate"] >= delta:
        c["outcome"] = "VOID"
        c["reason"] = ("V2 is VOID: random expiry would itself meet R1a against C "
                       "(CI lower bound > 0 and point estimate >= 0.08)")
    else:
        c["outcome"] = "inconclusive"
        c["reason"] = ("V2 inconclusive: the CI of V2 - C reaches beyond +/-0.08 without meeting "
                       "the VOID condition, so the headline verdict is capped at undetermined")
    return c


# ---------------------------------------------------------------------------
# Decision rules (§12)
# ---------------------------------------------------------------------------


def rule_of(estimate: float, lo: float, hi: float, delta: float = DELTA_F) -> Tuple[str, str]:
    """R1a, R1b, R3, R2, R4 in the registered order (the V row is separate)."""
    if lo > 0 and estimate >= delta:
        return "R1a", LABEL_R1A
    if lo > 0:
        return "R1b", LABEL_R1B
    if hi < 0:
        return "R3", LABEL_R3
    if lo >= -delta and hi <= delta:
        return "R2", LABEL_R2
    return "R4", LABEL_R4


def _outcome(check: Any) -> Optional[str]:
    if check is None:
        return None
    if isinstance(check, str):
        return check
    return check.get("outcome")


def decide(primary: Dict[str, Any], v1: Any, v2: Any, saturation: Any = False,
           role: str = "primary") -> Dict[str, Any]:
    """Apply §12 in order. ``headline`` is the only label that may be stated."""
    rule, label = rule_of(primary["estimate"], primary["lo"], primary["hi"])
    if isinstance(saturation, dict):
        saturated = bool(saturation.get("saturated"))
    else:
        saturated = bool(saturation)
    half_width = float(primary["half_width"])
    v1_out, v2_out = _outcome(v1), _outcome(v2)
    out: Dict[str, Any] = {
        "role": role,
        "rule": rule,
        "label": label,
        "headline": label,
        "reason": "",
        "information_rule": rule,
        "information_label": label,
        "v1": v1_out,
        "v2": v2_out,
        "half_width": half_width,
        "noisier_than_planned": half_width > HALF_WIDTH_FLAG,
        "saturation": saturated,
    }
    if role != "primary":
        # Secondary contrasts never carry a headline (§12).
        out["headline"] = None
        return out
    reasons: List[str] = []
    if v1_out == "fail":
        reasons.append("V1 failed: the CI of C - V1 on pre_ref_SR does not have lower bound > 0")
    if v2_out == "VOID":
        reasons.append("V2 is VOID: random expiry would itself meet R1a against C")
    if reasons:
        out.update(rule="V", label=LABEL_VOID, headline=LABEL_VOID,
                   information_rule=rule, information_label=label,
                   reason="; ".join(reasons))
        return out
    if v2_out == "inconclusive":
        out.update(headline=LABEL_R4,
                   reason="V2 inconclusive: the headline verdict is capped at undetermined")
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

#: Secondary contrasts of §8.2/§12: (name, left, right, metric).
SECONDARY_CONTRASTS: Tuple[Tuple[str, str, str, str], ...] = (
    ("D-C:full_sequence_SR", "D", "C", "full_sequence_sr"),
    ("D-C:pre_shift_SR", "D", "C", "pre_shift_sr"),
    ("D-V2:recovery_window_SR", "D", "V2", "recovery_window_sr"),
    ("C-A:recovery_window_SR", "C", "A", "recovery_window_sr"),
)


def secondary_contrasts(runs: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B) -> List[Dict[str, Any]]:
    """The §12 table applied to each secondary contrast, labelled secondary."""
    out: List[Dict[str, Any]] = []
    for name, left, right, metric in SECONDARY_CONTRASTS:
        c = contrast(runs, left, right, metric, name, b=b)
        d = decide(c, None, None, role="secondary")
        c["role"] = "secondary"
        c["rule"] = d["rule"]
        c["label"] = d["label"]
        out.append(c)
    return out


def _stratum(runs: Sequence[Dict[str, Any]], name: str, keep: Any, b: int) -> Optional[Dict[str, Any]]:
    subset = [r for r in runs if keep(r)]
    if not subset:
        return None
    c = contrast(subset, "D", "C", "recovery_window_sr", name, b=b)
    d = decide(c, None, None, role="secondary")
    c.update(role="secondary", rule=d["rule"], label=d["label"], runs=len(subset))
    return c


def strata_contrasts(runs: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B) -> Dict[str, Dict[str, Any]]:
    """D - C on recovery_window_SR within the shift and bump strata (§8.2, §12)."""
    return {
        "shift": {
            "detected": _stratum(runs, "D-C:detected_shift", lambda r: not r.get("silent"), b),
            "silent": _stratum(runs, "D-C:silent_shift", lambda r: bool(r.get("silent")), b),
        },
        "bump": {
            "yes": _stratum(runs, "D-C:benign_bump", lambda r: bool(r.get("bump")), b),
            "no": _stratum(runs, "D-C:no_benign_bump", lambda r: not r.get("bump"), b),
        },
    }


def arm_table(runs: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-arm descriptive table of §8.2 (no verdict rests on it)."""
    labels = [a for a in ARM_ORDER if all(a in r["arms"] for r in runs)]
    table: Dict[str, Dict[str, Any]] = {}
    n = len(runs)
    for label in labels:
        rows = [r["arms"][label] for r in runs]
        ttr = [row["tasks_to_recover"] for row in rows]
        censored = [row.get("tasks_to_recover_censored") for row in rows]
        changes = sum(row.get("policy_changes", 0) for row in rows)
        attributed = sum(row.get("attributed_changes", 0) for row in rows)
        fp = sum(row.get("false_positive_expiries", 0) for row in rows)
        table[label] = {
            "arm": label,
            "runs": n,
            "recovery_window_sr": _mean([row["recovery_window_sr"] for row in rows]),
            "pre_shift_sr": _mean([row["pre_shift_sr"] for row in rows]),
            "pre_ref_sr": _mean([row["pre_ref_sr"] for row in rows]),
            "full_sequence_sr": _mean([row["full_sequence_sr"] for row in rows]),
            "tasks_to_recover_mean": _mean([min(v, TASKS_TO_RECOVER_CENSOR) for v in ttr]),
            "tasks_to_recover_median": _median(ttr),
            "tasks_to_recover_censored": sum(1 for c in censored if c),
            "stale_policy_uses_window": _mean([row.get("stale_policy_uses_window", 0) for row in rows]),
            "stale_policy_uses_total": _mean([row.get("stale_policy_uses_total", 0) for row in rows]),
            "extra_tool_calls_window": _mean([row.get("extra_tool_calls", 0) for row in rows]),
            "false_positive_expiries_total": fp,
            "false_positive_expiries_per_run": fp / n if n else None,
            "expiry_events": sum(row.get("expiry_events", 0) for row in rows),
            "total_tokens": _mean([row.get("total_tokens", 0) for row in rows]),
            "expiry_overhead_tokens": _mean([row.get("expiry_overhead_tokens", 0) for row in rows]),
            "policy_changes": changes,
            "attributed_changes": attributed,
            "attribution_rate": (attributed / changes) if changes else None,
        }
    return table


def saturation_check(runs: Sequence[Dict[str, Any]],
                     table: Optional[Dict[str, Dict[str, Any]]] = None,
                     band: Tuple[float, float] = SATURATION_BAND) -> Dict[str, Any]:
    """§9.4: realised C or D recovery_window_SR mean outside [0.10, 0.90]."""
    table = table if table is not None else arm_table(runs)
    means = {a: table[a]["recovery_window_sr"] for a in ("C", "D") if a in table}
    outside = [a for a, v in means.items() if v is None or v < band[0] or v > band[1]]
    return {
        "band": [band[0], band[1]],
        "means": means,
        "planned": dict(PLANNED_RECOVERY_WINDOW_SR),
        "saturated": bool(outside),
        "arms_outside": outside,
    }


def observed_rates(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Observed silent-shift and benign-bump rates against the planned 25% / 50%."""
    n = len(runs)
    silent = sum(1 for r in runs if r.get("silent"))
    bump = sum(1 for r in runs if r.get("bump"))
    return {
        "runs": n,
        "silent_shift_rate": silent / n if n else None,
        "detected_shift_rate": (n - silent) / n if n else None,
        "benign_bump_rate": bump / n if n else None,
        "planned_silent_shift_rate": PLANNED_SILENT_SHIFT_RATE,
        "planned_benign_bump_rate": PLANNED_BENIGN_BUMP_RATE,
    }


def analyse(run_endpoint_list: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B) -> Dict[str, Any]:
    """Everything §8-§12 asks for, as one JSON-serialisable dict.

    ``primary``, ``v1``, ``v2`` and ``decision`` carry the verdict; ``secondary``,
    ``strata`` and ``arms`` are information only.
    """
    runs = list(run_endpoint_list)
    if not runs:
        raise ValueError("analyse() needs at least one run")
    primary = contrast(runs, "D", "C", "recovery_window_sr", "D-C:recovery_window_SR", b=b)
    v1 = v1_check(runs, b=b)
    v2 = v2_check(runs, b=b)
    table = arm_table(runs)
    sat = saturation_check(runs, table)
    dec = decide(primary, v1, v2, sat, role="primary")
    primary.update(role="primary", rule=dec["rule"], label=dec["label"],
                   headline=dec["headline"])
    ttr = contrast(runs, "D", "C", "tasks_to_recover",
                   "D-C:tasks_to_recover(censored at 75)", b=b)
    # §8.2: reported, no verdict -- the §12 rule table does not apply to task counts.
    ttr.update(role="secondary", rule=RULE_NO_VERDICT, label=LABEL_NO_VERDICT_TASKS)
    seeds: Dict[str, int] = {}
    for r in runs:
        seeds[str(r["seed"])] = seeds.get(str(r["seed"]), 0) + 1
    return {
        "n_runs": len(runs),
        "seeds": dict(sorted(seeds.items())),
        "margin": DELTA_F,
        "half_width_flag": HALF_WIDTH_FLAG,
        "half_width": primary["half_width"],
        "noisier_than_planned": dec["noisier_than_planned"],
        "bootstrap": {
            "b": b,
            "method": "paired percentile bootstrap over runs, stratified by seed, 95%",
            "seed": "stable_int('expiry-bootstrap', contrast)",
        },
        "primary": primary,
        "v1": v1,
        "v2": v2,
        "decision": dec,
        "secondary": secondary_contrasts(runs, b=b),
        "strata": strata_contrasts(runs, b=b),
        "per_seed": primary["per_seed"],
        "arms": table,
        "tasks_to_recover": {"D-C": ttr, "per_arm": {
            a: {"mean": table[a]["tasks_to_recover_mean"],
                "median": table[a]["tasks_to_recover_median"],
                "n_censored": table[a]["tasks_to_recover_censored"]}
            for a in table}},
        "observed": observed_rates(runs),
        "saturation": sat,
    }
