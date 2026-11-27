"""Analysis and decision rules of the budget experiment (PREREG_BUDGET §8, §10-§14).

The unit of analysis is the run (100 tasks, 40 candidates). Contrasts are paired per
run: every arm of a run shares the stream, the arrival order and the task draws. CIs
are a percentile bootstrap over runs, stratified by seed (runs resampled within each of
the four seeds, the same resampled runs for both arms of a contrast), B = 2000, 95%,
seeded with ``stable_int("budget-bootstrap", contrast)`` -- one seed per contrast, so
every statistic of a contrast (tokens, ``Δ_SR``, harm, token parts) is computed on the
same resampled runs and the denominator of the token ratio is recomputed on each
resample (§8.1).

The primary contrast is D2 ``repro_guarded_fast_path`` vs C ``always_full_gate`` on
total run tokens, jointly with ``plateau_SR`` non-inferiority (§8.1.2) and the
harmful-absorption guardrail (§8.1.3). Only that contrast produces the headline. D1 vs
C, D2 vs V2 and every contrast split by stratum (``k_s`` = 1/0, ``k_e`` = 1/0) are
labelled secondary. C vs A and the §8.3 endpoints are reported with rule ``n/a`` and
carry no verdict wording.

Nothing here is normative: the result is one simulated tool (``web_search`` timeout
recovery) under one simulated stream, with synthetic peers and forged claims.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from experiments.timeout_recovery.host import stable_int

from .stream import N_CANDIDATES, X_PLAN
from .sim import ARMS, ARM_ORDER

# ---------------------------------------------------------------------------
# Constants (§10, §12)
# ---------------------------------------------------------------------------

#: §12: bootstrap resamples.
BOOTSTRAP_B = 2000

#: §10 margins. No other margin appears in the pre-registration.
DELTA_TOK = 0.10
DELTA_SR = 0.05
C_HARM = 0.10

#: §10 noise floor: realised primary token half-width above this is flagged; the
#: realised ``Δ_SR`` half-width is merely reported against 0.026.
TOK_HALF_WIDTH_FLAG = 0.039
SR_HALF_WIDTH_PLAN = 0.026

#: §9.5 / §16 item 22: a realised C or D2 mean ``plateau_SR`` outside this band is
#: reported as saturation, without changing any rule.
SATURATION_BOUNDS = (0.10, 0.90)

CONTROL_ARM = "C"
PRIMARY_ARM = "D2"
D1_ARM = "D1"
V1_ARM = "V1"
V2_ARM = "V2"
NO_SHARING_ARM = "A"

#: Verdict labels, verbatim from §12.
LABEL_VOID = "VOID"
LABEL_R1A = ("supported: the repro fast path reduces total run tokens by at least the "
             "margin, with non-inferior plateau SR and harm within the cap")
LABEL_R1B = "positive but below the 0.10 token margin; not practically significant"
LABEL_R3_PREFIX = "not supported: the fast path fails the joint criterion"
LABEL_R2 = "equivalent within ±0.10 relative total tokens"
LABEL_R4 = "undetermined"

#: §8.3 wording for endpoints that are reported without a verdict.
RULE_NO_VERDICT = "n/a"
LABEL_REPORTED = "reported, no verdict (§8.3)"

#: R3 reason codes, in the registered order (§12, §16 item 21).
REASON_MORE_TOKENS = "more_tokens"
REASON_NOT_NI = "plateau_SR_not_non_inferior"
REASON_HARM = "harm_cap_breached"
REASON_ORDER = (REASON_MORE_TOKENS, REASON_NOT_NI, REASON_HARM)

#: Statistics carried by every contrast. The first group is reduced with the §8.1
#: ratio of paired sums, the second group as a mean of paired per-run differences.
RATIO_METRICS: Tuple[str, ...] = ("total_tokens", "task_tokens", "gate_tokens",
                                  "repro_overhead_tokens", "verification_tokens")
DIFF_METRICS: Tuple[str, ...] = ("plateau_SR", "full_sequence_SR", "harmful_absorbed")
N_RATIO = len(RATIO_METRICS)
N_COLUMNS = 2 * N_RATIO + len(DIFF_METRICS)

#: §8.3 token parts reported for D2 vs C, D1 vs C and V2 vs C (§8.1 formula).
TOKEN_PARTS: Tuple[str, ...] = ("gate_tokens", "repro_overhead_tokens",
                                "verification_tokens", "task_tokens")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _r(x: float) -> float:
    """Round a reported float consistently (6 d.p.)."""
    return round(float(x) + 0.0, 6)


def _mean(xs: Sequence[float]) -> Optional[float]:
    return _r(sum(xs) / len(xs)) if xs else None


def _arm_order(arms: Sequence[str]) -> List[str]:
    return [a for a in ARM_ORDER if a in arms] + sorted(a for a in arms if a not in ARM_ORDER)


def normalise_runs(per_run_endpoints: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Accept ``simulate_run`` records or bare ``run_endpoints`` dicts (§8 unit = run).

    Each returned record is ``{"seed", "run", "k_e", "k_s", "arms": {arm: endpoint}}``.
    A bare ``{arm: endpoint}`` dict is used as is; ``seed``/``run``/``k_e``/``k_s`` are
    taken from the record, else from an endpoint.
    """
    out: List[Dict[str, Any]] = []
    for index, rec in enumerate(per_run_endpoints):
        if not isinstance(rec, dict):
            raise TypeError(f"run record {index} is not a dict")
        arms = rec.get("arms")
        if not isinstance(arms, dict):
            arms = {k: v for k, v in rec.items()
                    if isinstance(v, dict) and "plateau_SR" in v}
        if not arms:
            raise ValueError(f"run record {index} carries no arm endpoints")
        first = arms[_arm_order(arms)[0]]
        control = arms.get(CONTROL_ARM, first)
        seed = rec.get("seed", first.get("seed", index))
        run = rec.get("run", first.get("run", index))
        if isinstance(control.get("seed"), int):
            seed = control["seed"]
        if isinstance(control.get("run"), int):
            run = control["run"]
        out.append({
            "seed": seed,
            "run": run,
            "k_e": int(rec.get("k_e", control.get("k_e", 0)) or 0),
            "k_s": int(rec.get("k_s", control.get("k_s", 0)) or 0),
            "arms": arms,
        })
    return out


# ---------------------------------------------------------------------------
# Paired stratified percentile bootstrap (§8.1, §12)
# ---------------------------------------------------------------------------


def _row(left: Dict[str, Any], right: Dict[str, Any]) -> Tuple[float, ...]:
    """The per-run paired record: X sums, C sums, then the paired differences."""
    row = [float(left[m]) for m in RATIO_METRICS]
    row.extend(float(right[m]) for m in RATIO_METRICS)
    for m in DIFF_METRICS:
        row.append(float(left[m]) - float(right[m]))
    return tuple(row)


def _stats(acc: Sequence[float], n: int) -> Dict[str, float]:
    """§8.1: ratio of paired sums for token counts, mean paired difference for the rest."""
    out: Dict[str, float] = {}
    for i, m in enumerate(RATIO_METRICS):
        c = acc[N_RATIO + i]
        out[m] = 1.0 - acc[i] / c if c else 0.0
    for j, m in enumerate(DIFF_METRICS):
        out[m] = acc[2 * N_RATIO + j] / n if n else 0.0
    return out


def _percentiles(vals: List[float], b: int) -> Tuple[float, float]:
    s = sorted(vals)
    if not s:
        return 0.0, 0.0
    lo_idx = min(max(int(0.025 * b), 0), len(s) - 1)
    hi_idx = min(max(int(0.975 * b) - 1, 0), len(s) - 1)
    return s[lo_idx], s[max(hi_idx, lo_idx)]


def paired_bootstrap(runs: Sequence[Dict[str, Any]], left: str, right: str,
                     name: str, b: int = BOOTSTRAP_B
                     ) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Any]]:
    """Point estimate, 95% CI and half-width of every statistic of one contrast.

    Runs are resampled within each seed, the same resampled runs for both arms, so the
    numerator and the denominator of the §8.1 token ratio are recomputed together on
    every resample. If any stratum (seed) holds fewer than two runs of the contrast,
    the scope is resampled as a single pool instead of crashing (§12: never crash).
    """
    usable = [r for r in runs if left in r["arms"] and right in r["arms"]]
    meta: Dict[str, Any] = {
        "contrast": name,
        "left": left,
        "right": right,
        "runs": len(usable),
        "b": b,
        "seed": f"stable_int('budget-bootstrap', {name!r})",
        "stratified": True,
        "note": "",
    }
    metrics = RATIO_METRICS + DIFF_METRICS
    if not usable:
        zero = {"estimate": 0.0, "lo": 0.0, "hi": 0.0, "half_width": 0.0}
        meta.update(runs=0, stratified=False, per_seed={}, undefined_parts=[],
                    left_means={m: None for m in RATIO_METRICS},
                    right_sums={m: 0.0 for m in RATIO_METRICS},
                    note="no run carries both arms of this contrast; estimates are 0.0")
        return {m: dict(zero) for m in metrics}, meta

    rows_by_seed: Dict[Any, List[Tuple[float, ...]]] = {}
    for r in usable:
        rows_by_seed.setdefault(r["seed"], []).append(_row(r["arms"][left], r["arms"][right]))
    seed_keys = sorted(rows_by_seed, key=str)
    sizes = {s: len(rows_by_seed[s]) for s in seed_keys}
    counts = {str(s): sizes[s] for s in seed_keys}
    groups = {s: rows_by_seed[s] for s in seed_keys}
    if min(sizes.values()) < 2:
        pooled = [row for s in seed_keys for row in rows_by_seed[s]]
        groups = {"*": pooled}
        sizes = {"*": len(pooled)}
        seed_keys = ["*"]
        meta.update(stratified=False,
                    note=("a stratum holds fewer than 2 runs: the contrast is resampled "
                          "as one pool (unstratified fallback), never a crash"))
    cols = {s: tuple(tuple(row[j] for row in groups[s]) for j in range(N_COLUMNS))
            for s in seed_keys}

    rng = random.Random(stable_int("budget-bootstrap", name))
    n = len(usable)
    draws: Dict[str, List[float]] = {m: [] for m in metrics}
    for _ in range(b):
        acc = [0.0] * N_COLUMNS
        for s in seed_keys:
            m_s = sizes[s]
            idx = [rng.randrange(m_s) for _ in range(m_s)]
            for j, col in enumerate(cols[s]):
                acc[j] += sum(map(col.__getitem__, idx))
        for stat_name, value in _stats(acc, n).items():
            draws[stat_name].append(value)

    total = [0.0] * N_COLUMNS
    for r in usable:
        for j, v in enumerate(_row(r["arms"][left], r["arms"][right])):
            total[j] += v
    point = _stats(total, n)

    out: Dict[str, Optional[Dict[str, float]]] = {}
    for m in metrics:
        lo, hi = _percentiles(draws[m], b)
        out[m] = {"estimate": _r(point[m]), "lo": _r(lo), "hi": _r(hi),
                  "half_width": _r((hi - lo) / 2)}
    # A ratio of paired sums is undefined when the right-hand arm sums to zero for that
    # metric (e.g. C is never charged ``repro_overhead_tokens``): report ``None``, never
    # a spurious ``1 - 0/0 = 0``. The left-hand mean is kept for the "n/a" wording.
    undefined = [m for i, m in enumerate(RATIO_METRICS) if not total[N_RATIO + i]]
    for m in undefined:
        out[m] = None
    meta["undefined_parts"] = undefined
    meta["left_means"] = {m: _mean([float(r["arms"][left][m]) for r in usable])
                          for m in RATIO_METRICS}
    meta["right_sums"] = {m: _r(total[N_RATIO + i]) for i, m in enumerate(RATIO_METRICS)}
    meta["per_seed"] = counts
    return out, meta


def per_seed_estimates(runs: Sequence[Dict[str, Any]], left: str,
                       right: str) -> Dict[str, Dict[str, Optional[float]]]:
    """Per-seed means of the same three primary statistics (no per-seed verdict, §12)."""
    usable = [r for r in runs if left in r["arms"] and right in r["arms"]]
    buckets: Dict[Any, List[Tuple[float, ...]]] = {}
    for r in usable:
        buckets.setdefault(r["seed"], []).append(_row(r["arms"][left], r["arms"][right]))
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for seed in sorted(buckets, key=str):
        rows = buckets[seed]
        total = [sum(row[j] for row in rows) for j in range(N_COLUMNS)]
        st = _stats(total, len(rows))
        out[str(seed)] = {"runs": len(rows),
                          "rel_token_reduction": _r(st["total_tokens"]),
                          "delta_SR": _r(st["plateau_SR"]),
                          "harm_difference": _r(st["harmful_absorbed"])}
    return out


# ---------------------------------------------------------------------------
# Contrasts
# ---------------------------------------------------------------------------


def _outcome(check: Any) -> Optional[str]:
    if check is None:
        return None
    if isinstance(check, str):
        return check
    return check.get("outcome")


def contrast(runs: Sequence[Dict[str, Any]], left: str, right: str, name: str,
             role: str = "secondary", b: int = BOOTSTRAP_B, apply_rule: bool = True,
             note: str = "") -> Dict[str, Any]:
    """One paired contrast: the three joint statistics of §8.1 plus the §8.3 parts."""
    stats, meta = paired_bootstrap(runs, left, right, name, b=b)
    tok = stats["total_tokens"]
    dsr = stats["plateau_SR"]
    harm = stats["harmful_absorbed"]
    non_inferior = dsr["lo"] > -DELTA_SR
    safe = harm["hi"] <= C_HARM
    rule, label, reasons = rule_of(tok, non_inferior, safe)
    if not apply_rule:
        rule, label, reasons = RULE_NO_VERDICT, LABEL_REPORTED, []
    return {
        "contrast": name,
        "left": left,
        "right": right,
        "role": role,
        "runs": meta["runs"],
        "b": meta["b"],
        "bootstrap_seed": meta["seed"],
        "stratified": meta["stratified"],
        "bootstrap_note": meta["note"] or note,
        "tokens": {
            "rel_token_reduction": tok,
            "noisier_than_planned": tok["half_width"] > TOK_HALF_WIDTH_FLAG,
            "half_width_flag": TOK_HALF_WIDTH_FLAG,
            "formula": "1 - sum(total_tokens(X)) / sum(total_tokens(C)) over the paired runs",
        },
        "delta_SR": {
            "estimate": dsr["estimate"], "lo": dsr["lo"], "hi": dsr["hi"],
            "half_width": dsr["half_width"], "half_width_plan": SR_HALF_WIDTH_PLAN,
            "margin": DELTA_SR, "non_inferior": non_inferior,
        },
        "harm": {
            "estimate": harm["estimate"], "lo": harm["lo"], "hi": harm["hi"],
            "half_width": harm["half_width"], "ci_upper": harm["hi"],
            "cap": C_HARM, "safe": safe,
        },
        # §8.3: reported, no verdict -- never carries an R1a/R1b/R2/R3 label.
        # A part whose C-side sum is 0 is undefined and reported as ``None``.
        "parts": {p: (None if stats[p] is None else dict(stats[p])) for p in TOKEN_PARTS},
        "parts_left_means": {p: (meta.get("left_means") or {}).get(p) for p in TOKEN_PARTS},
        "parts_undefined": [p for p in TOKEN_PARTS if stats[p] is None],
        "full_sequence_SR": dict(stats["full_sequence_SR"]),
        "per_seed": per_seed_estimates(runs, left, right),
        "rule": rule,
        "label": label,
        "reasons": reasons,
        "headline": None,
        "rule_as_information": rule,
        "label_as_information": label,
    }


def rule_of(tokens: Dict[str, float], non_inferior: bool, safe: bool,
            delta_tok: float = DELTA_TOK) -> Tuple[str, str, List[str]]:
    """§12 rows R1a, R1b, R3, R2, R4 in the registered order (row V is separate)."""
    lo, hi, est = tokens["lo"], tokens["hi"], tokens["estimate"]
    reasons: List[str] = []
    if hi < 0:
        reasons.append(REASON_MORE_TOKENS)
    if not non_inferior:
        reasons.append(REASON_NOT_NI)
    if not safe:
        reasons.append(REASON_HARM)
    if lo > 0 and non_inferior and safe:
        if est >= delta_tok:
            return "R1a", LABEL_R1A, []
        return "R1b", LABEL_R1B, []
    if reasons:
        ordered = [r for r in REASON_ORDER if r in reasons]
        return "R3", f"{LABEL_R3_PREFIX} ({', '.join(ordered)})", ordered
    if lo >= -delta_tok and hi <= delta_tok and non_inferior and safe:
        return "R2", LABEL_R2, []
    return "R4", LABEL_R4, []


def decide(primary: Dict[str, Any], v1: Any = None, v2: Any = None,
           saturation: Any = False) -> Dict[str, Any]:
    """Apply §12 to the primary contrast. ``headline`` is the only stateable verdict."""
    if isinstance(saturation, dict):
        saturated = bool(saturation.get("saturated"))
    else:
        saturated = bool(saturation)
    v1_out, v2_out = _outcome(v1), _outcome(v2)
    info_rule = primary["rule_as_information"]
    info_label = primary["label_as_information"]
    tok_hw = float(primary["tokens"]["rel_token_reduction"]["half_width"])
    out: Dict[str, Any] = {
        "role": "primary",
        "rule": info_rule,
        "label": info_label,
        "headline": info_label,
        "rule_as_information": info_rule,
        "label_as_information": info_label,
        "reasons": list(primary.get("reasons", [])),
        "reason": "",
        "v1": v1_out,
        "v2": v2_out,
        "v2_components": (v2 or {}).get("components", {}) if isinstance(v2, dict) else {},
        "tokens_half_width": tok_hw,
        "noisier_than_planned": tok_hw > TOK_HALF_WIDTH_FLAG,
        "delta_SR_half_width": primary["delta_SR"]["half_width"],
        "delta_SR_half_width_plan": SR_HALF_WIDTH_PLAN,
        "saturation": saturated,
    }
    reasons: List[str] = []
    if v1_out == "fail":
        reasons.append("V1 failed: the CI of C - V1 on plateau_SR does not have lower "
                       "bound > 0, so absorbed content does not reach SR")
    if v2_out == "VOID":
        reasons.append("V2 is VOID: random skipping would itself meet the full R1a "
                       "condition against C")
    if reasons:
        out.update(rule="V", label=LABEL_VOID, headline=LABEL_VOID,
                   reason="; ".join(reasons))
        return out
    if v2_out == "inconclusive":
        out.update(headline=LABEL_R4,
                   reason=("V2 inconclusive: the rule is reported as information and the "
                           "headline verdict is capped at undetermined"))
    return out


# ---------------------------------------------------------------------------
# Validity checks (§11)
# ---------------------------------------------------------------------------


def v1_check(runs: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B) -> Dict[str, Any]:
    """§11.1: pass iff the CI lower bound of ``plateau_SR(C) - plateau_SR(V1)`` is > 0."""
    stats, meta = paired_bootstrap(runs, CONTROL_ARM, V1_ARM, "C-V1:plateau_SR", b=b)
    gap = dict(stats["plateau_SR"])
    outcome = "pass" if gap["lo"] > 0 else "fail"
    out: Dict[str, Any] = {
        "check": "V1",
        "contrast": "C-V1",
        "left": CONTROL_ARM,
        "right": V1_ARM,
        "metric": "plateau_SR",
        "plateau_sr_gap": gap,
        "runs": meta["runs"],
        "stratified": meta["stratified"],
        "bootstrap_seed": meta["seed"],
        "bootstrap_note": meta["note"],
        "outcome": outcome,
        "reason": "",
    }
    if outcome != "pass":
        out["reason"] = ("V1 failed: the CI of C - V1 on plateau_SR does not have lower "
                         "bound > 0, so absorbed content does not reach SR and the "
                         "result is VOID")
    return out


def v2_check(runs: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B) -> Dict[str, Any]:
    """§11.2: VOID / pass / inconclusive for ``V2 - C``, with the three components."""
    c = contrast(runs, V2_ARM, CONTROL_ARM, "V2-C", role="validity", b=b)
    components = {
        "plateau_sr_ci_upper_below_zero": c["delta_SR"]["hi"] < 0,
        "token_ci_upper_below_delta_tok": c["tokens"]["rel_token_reduction"]["hi"] < DELTA_TOK,
        "harm_ci_lower_above_c_harm": c["harm"]["lo"] > C_HARM,
    }
    out: Dict[str, Any] = {
        "check": "V2",
        "contrast": "V2-C",
        "left": V2_ARM,
        "right": CONTROL_ARM,
        "runs": c["runs"],
        "criterion": {
            "tokens": c["tokens"],
            "delta_SR": c["delta_SR"],
            "harm": c["harm"],
            "non_inferior": c["delta_SR"]["non_inferior"],
            "safe": c["harm"]["safe"],
            "rule_as_information": c["rule_as_information"],
        },
        "components": components,
        "reason": "",
    }
    if c["rule_as_information"] == "R1a":
        out["outcome"] = "VOID"
        out["reason"] = ("V2 is VOID: random skipping would itself meet the full R1a "
                         "condition against C")
    elif any(components.values()):
        out["outcome"] = "pass"
    else:
        out["outcome"] = "inconclusive"
        out["reason"] = ("V2 inconclusive: random skipping does not fail any joint "
                         "component decisively, so the headline verdict is capped at "
                         "undetermined")
    out["components_hold"] = [k for k, v in components.items() if v]
    out["contrast_detail"] = c
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

#: Secondary contrasts of §12: same rule table, labelled secondary, never a headline.
SECONDARY_CONTRAST_ARMS: Tuple[Tuple[str, str], ...] = ((D1_ARM, CONTROL_ARM),
                                                        (PRIMARY_ARM, V2_ARM))

#: Contrasts split by stratum (§8.3: descriptive, no verdict on the stratum alone).
STRATA_CONTRAST_ARMS: Tuple[Tuple[str, str], ...] = ((PRIMARY_ARM, CONTROL_ARM),
                                                     (D1_ARM, CONTROL_ARM))

STRATA_KEYS: Tuple[str, ...] = ("k_s", "k_e")

#: Plain-English meaning of each stratum level (prereg §5.2: ``k_e`` = effective packet
#: present, ``k_s`` = dishonest peer present), so no table row is read from a bare flag.
STRATUM_LABELS: Dict[Tuple[str, int], str] = {
    ("k_s", 1): "dishonest peer present (k_s=1)",
    ("k_s", 0): "dishonest peer absent (k_s=0)",
    ("k_e", 1): "effective packet present (k_e=1)",
    ("k_e", 0): "effective packet absent (k_e=0)",
}

#: §8.3 contrasts whose token parts and ``full_sequence_SR`` are reported, no verdict.
REPORT_ONLY_CONTRAST_ARMS: Tuple[Tuple[str, str], ...] = ((PRIMARY_ARM, CONTROL_ARM),
                                                          (D1_ARM, CONTROL_ARM),
                                                          (V2_ARM, CONTROL_ARM))


def secondary_contrasts(runs: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B
                        ) -> List[Dict[str, Any]]:
    return [contrast(runs, left, right, f"{left}-{right}", role="secondary", b=b)
            for left, right in SECONDARY_CONTRAST_ARMS]


def strata_contrasts(runs: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B
                     ) -> List[Dict[str, Any]]:
    """Every secondary contrast split by ``k_s`` = 1/0 and ``k_e`` = 1/0 (§8.3)."""
    out: List[Dict[str, Any]] = []
    for left, right in STRATA_CONTRAST_ARMS:
        for key in STRATA_KEYS:
            for level in (1, 0):
                name = f"{left}-{right}:{key}={level}"
                stratum_label = STRATUM_LABELS[(key, level)]
                scoped = [r for r in runs if r[key] == level]
                if not scoped:
                    out.append({
                        "contrast": name, "left": left, "right": right,
                        "stratum": {key: level}, "stratum_label": stratum_label,
                        "role": "secondary", "runs": 0, "rule": RULE_NO_VERDICT,
                        "label": LABEL_REPORTED, "headline": None,
                        "note": "empty stratum: no run of this scope, nothing to estimate",
                    })
                    continue
                c = contrast(scoped, left, right, name, role="secondary", b=b)
                c["stratum"] = {key: level}
                c["stratum_label"] = stratum_label
                out.append(c)
    return out


def _arm_endpoints(runs: Sequence[Dict[str, Any]], arm: str) -> List[Dict[str, Any]]:
    return [r["arms"][arm] for r in runs if arm in r["arms"]]


def arm_table(runs: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-arm means of §8.1/§8.2 figures (descriptive; no verdict)."""
    arms: List[str] = []
    for r in runs:
        for arm in _arm_order(list(r["arms"])):
            if arm not in arms:
                arms.append(arm)
    table: Dict[str, Dict[str, Any]] = {}
    for arm in arms:
        eps = _arm_endpoints(runs, arm)
        if not eps:
            continue
        table[arm] = {
            "arm": arm,
            "name": ARMS.get(arm, ""),
            "runs": len(eps),
            "plateau_SR_mean": _mean([e["plateau_SR"] for e in eps]),
            "full_sequence_SR_mean": _mean([e["full_sequence_SR"] for e in eps]),
            "total_tokens_mean": _mean([e["total_tokens"] for e in eps]),
            "task_tokens_mean": _mean([e["task_tokens"] for e in eps]),
            "gate_tokens_mean": _mean([e["gate_tokens"] for e in eps]),
            "repro_overhead_tokens_mean": _mean([e["repro_overhead_tokens"] for e in eps]),
            "verification_tokens_mean": _mean([e["verification_tokens"] for e in eps]),
            "harmful_absorption_rate": _mean([1.0 if e["harmful_absorbed"] else 0.0
                                              for e in eps]),
            "persistent_harm_count": sum(1 for e in eps if e.get("persistent_harm")),
            "tasks_on_harmful_policy_mean": _mean([e.get("tasks_on_harmful_policy", 0)
                                                   for e in eps]),
            "tasks_on_harmful_policy_total": sum(e.get("tasks_on_harmful_policy", 0)
                                                 for e in eps),
            "fast_path_rate_mean": _mean([e.get("fast_path_rate", 0.0) for e in eps]),
        }
    return table


def _count(ep: Dict[str, Any], key: str, family: Optional[str] = None) -> int:
    value = ep.get(key, 0)
    if isinstance(value, dict):
        if family is None:
            return int(sum(v for v in value.values() if isinstance(v, (int, float))))
        return int(value.get(family, 0) or 0)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def report_only_metrics(runs: Sequence[Dict[str, Any]],
                        contrasts: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """§8.3 endpoints: reported, no verdict, no verdict wording anywhere."""
    families: List[str] = []
    for r in runs:
        for ep in r["arms"].values():
            for fam in sorted(ep.get("spot_check_pass") or {}):
                if fam not in families:
                    families.append(fam)
    families.sort()
    per_arm: Dict[str, Dict[str, Any]] = {}
    for arm in (_arm_order([a for r in runs for a in r["arms"]])):
        eps = _arm_endpoints(runs, arm)
        if not eps:
            continue
        events = sum(_count(e, "n_fast_reject") + _count(e, "n_fast_accept") for e in eps)
        wrong = sum(_count(e, "false_fast_path") for e in eps)
        per_run = [(e["false_fast_path"] / (e["n_fast_reject"] + e["n_fast_accept"]))
                   for e in eps if (e.get("n_fast_reject", 0) + e.get("n_fast_accept", 0))]
        per_arm[arm] = {
            "arm": arm,
            "name": ARMS.get(arm, ""),
            "runs": len(eps),
            "fast_path_rate_mean": _mean([e.get("fast_path_rate", 0.0) for e in eps]),
            "fast_path_events_total": events,
            "fast_reject_total": sum(_count(e, "n_fast_reject") for e in eps),
            "fast_accept_total": sum(_count(e, "n_fast_accept") for e in eps),
            "false_fast_path_total": wrong,
            "false_fast_path_rate": _r(wrong / events) if events else 0.0,
            "false_fast_path_rate_per_run_mean": _mean(per_run) or 0.0,
            "spot_check_pass": {fam: sum(_count(e, "spot_check_pass", fam) for e in eps)
                                for fam in families},
            "spot_check_fail": {fam: sum(_count(e, "spot_check_fail", fam) for e in eps)
                                for fam in families},
            "escalations_total": sum(_count(e, "escalations") for e in eps),
            "probation_rollbacks_true_total": sum(_count(e, "probation_rollbacks_true")
                                                  for e in eps),
            "probation_rollbacks_false_total": sum(_count(e, "probation_rollbacks_false")
                                                   for e in eps),
            "probation_superseded_total": sum(_count(e, "probation_superseded") for e in eps),
            "budget_rejections_total": sum(_count(e, "budget_rejections") for e in eps),
            "rollbacks_gate_total": sum(int((e.get("rollbacks") or {}).get("gate", 0))
                                        for e in eps),
            "rollbacks_probation_total": sum(int((e.get("rollbacks") or {}).get("probation", 0))
                                             for e in eps),
            "rollbacks_total": sum(int((e.get("rollbacks") or {}).get("total", 0))
                                   for e in eps),
            "tasks_on_harmful_policy_mean": _mean([e.get("tasks_on_harmful_policy", 0)
                                                   for e in eps]),
            "persistent_harm_runs": sum(1 for e in eps if e.get("persistent_harm")),
            "harmful_installations_total": sum(_count(e, "harmful_installations") for e in eps),
            "harmful_fast_accepted_total": sum(_count(e, "harmful_fast_accepted") for e in eps),
            "harmful_spot_check_passed_total": sum(_count(e, "harmful_spot_check_passed")
                                                   for e in eps),
            "harmful_gate_absorbed_total": sum(_count(e, "harmful_gate_absorbed") for e in eps),
            "harmful_rolled_back_total": sum(_count(e, "harmful_rolled_back") for e in eps),
            "resident_context_tokens_T100_mean": _mean(
                [e.get("resident_context_tokens_T100", 0) for e in eps]),
            "attribution_rate_mean": _mean([float((e.get("attributed") or {}).get("rate", 0.0))
                                            for e in eps]),
        }
    token_parts: Dict[str, Dict[str, Any]] = {}
    parts_left_means: Dict[str, Dict[str, Any]] = {}
    sequence: Dict[str, Dict[str, Any]] = {}
    for left, right in REPORT_ONLY_CONTRAST_ARMS:
        name = f"{left}-{right}"
        c = contrasts.get(name)
        if c is None:
            continue
        token_parts[name] = {p: c["parts"][p] for p in TOKEN_PARTS}
        parts_left_means[name] = {p: (c.get("parts_left_means") or {}).get(p)
                                  for p in TOKEN_PARTS}
        sequence[name] = c["full_sequence_SR"]
    return {
        "rule": RULE_NO_VERDICT,
        "label": LABEL_REPORTED,
        "note": ("§8.3 endpoints are reported with their estimate, CI and half-width only; "
                 "no R1a/R1b/R2/R3 label and no verdict wording is applied to them"),
        "token_parts_relative_reduction": token_parts,
        "parts_left_means": parts_left_means,
        "full_sequence_SR_difference": sequence,
        "per_arm": per_arm,
    }


def saturation_check(table: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """§9.5 / §16 item 22: realised C or D2 mean ``plateau_SR`` outside the band."""
    lo, hi = SATURATION_BOUNDS
    arms = {}
    for arm in (CONTROL_ARM, PRIMARY_ARM):
        mean = (table.get(arm) or {}).get("plateau_SR_mean")
        arms[arm] = {"plateau_SR_mean": mean,
                     "outside_bounds": None if mean is None else not (lo <= mean <= hi)}
    return {"bounds": [lo, hi], "arms": arms,
            "saturated": any(v["outside_bounds"] for v in arms.values()),
            "note": "saturation is reported; the decision rules are unchanged"}


def observed_rates(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Observed stream rates of §5.2 / §16 items 8 and 23.

    Per prereg §5.2 ``K_E`` is the *effective packet present* indicator and ``K_S`` the
    *dishonest peer present* indicator (``stream.stream_indicators``: ``k_e`` adds the
    effective packet and removes a no-``repro`` one, ``k_s`` adds the four spoofed
    packets). The semantic keys below name that mapping, so a renderer cannot swap the
    two rates; ``k_e_share``/``k_s_share`` are kept for the prereg variable names.
    """
    n = len(runs)
    k_e_share = _mean([1.0 if r["k_e"] == 1 else 0.0 for r in runs])
    k_s_share = _mean([1.0 if r["k_s"] == 1 else 0.0 for r in runs])
    return {
        "runs": n,
        "k_e_share": k_e_share,
        "k_s_share": k_s_share,
        "effective_packet_share": k_e_share,
        "dishonest_peer_share": k_s_share,
        "mismatched_share": _r(len(X_PLAN) / N_CANDIDATES),
        "mismatched_per_run": len(X_PLAN),
        "planned": {"k_e_share": 0.5, "k_s_share": 0.5, "mismatched_share": 0.25},
    }


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def c_vs_a(runs: Sequence[Dict[str, Any]], b: int = BOOTSTRAP_B) -> Dict[str, Any]:
    """C vs A: total tokens and ``plateau_SR`` differences only, reported, no verdict."""
    c = contrast(runs, CONTROL_ARM, NO_SHARING_ARM, "C-A", role="report_only", b=b,
                 apply_rule=False)
    return {
        "contrast": "C-A",
        "left": CONTROL_ARM,
        "right": NO_SHARING_ARM,
        "role": "report_only",
        "runs": c["runs"],
        "rule": RULE_NO_VERDICT,
        "label": LABEL_REPORTED,
        "headline": None,
        "tokens": c["tokens"],
        "delta_SR": c["delta_SR"],
        "per_seed": c["per_seed"],
        "note": ("C vs A: only total tokens and plateau_SR differences are reported "
                 "(§8.3); no rule is applied"),
    }


def analyse(per_run_endpoints: Sequence[Dict[str, Any]],
            b: int = BOOTSTRAP_B) -> Dict[str, Any]:
    """Full analysis of one set of runs; JSON-serialisable and deterministic.

    ``per_run_endpoints`` is a list of run records: either ``simulate_run`` dicts with
    an ``"arms"`` mapping or bare ``run_endpoints(seed, run)`` mappings.
    """
    runs = normalise_runs(per_run_endpoints)
    if not runs:
        raise ValueError("analyse() needs at least one run")

    primary = contrast(runs, PRIMARY_ARM, CONTROL_ARM, "D2-C", role="primary", b=b)
    secondary = secondary_contrasts(runs, b=b)
    v1 = v1_check(runs, b=b)
    v2_full = v2_check(runs, b=b)
    table = arm_table(runs)
    saturation = saturation_check(table)
    dec = decide(primary, v1, v2_full, saturation)
    primary.update(role="primary", rule=dec["rule"], label=dec["label"],
                   headline=dec["headline"], reasons=dec["reasons"],
                   reason=dec["reason"], rule_as_information=dec["rule_as_information"],
                   label_as_information=dec["label_as_information"])
    v2 = {k: v for k, v in v2_full.items() if k != "contrast_detail"}

    by_name = {c["contrast"]: c for c in secondary}
    contrasts = {"D2-C": primary, "D1-C": by_name.get("D1-C"),
                 "V2-C": v2_full.get("contrast_detail")}

    seeds: Dict[str, int] = {}
    for r in runs:
        seeds[str(r["seed"])] = seeds.get(str(r["seed"]), 0) + 1
    tok = primary["tokens"]["rel_token_reduction"]
    return {
        "n_runs": len(runs),
        "seeds": dict(sorted(seeds.items())),
        "margins": {"delta_tok": DELTA_TOK, "delta_sr": DELTA_SR, "c_harm": C_HARM},
        "half_width_flag": TOK_HALF_WIDTH_FLAG,
        "sr_half_width_plan": SR_HALF_WIDTH_PLAN,
        "half_width": tok["half_width"],
        "noisier_than_planned": bool(tok["half_width"] > TOK_HALF_WIDTH_FLAG),
        "delta_SR_half_width": primary["delta_SR"]["half_width"],
        "delta_SR_half_width_exceeds_plan": bool(
            primary["delta_SR"]["half_width"] > SR_HALF_WIDTH_PLAN),
        "saturation_bounds": list(SATURATION_BOUNDS),
        "bootstrap": {
            "b": b,
            "method": ("paired percentile bootstrap over runs, stratified by seed, 95%, "
                       "the same resampled runs for both arms of a contrast"),
            "seed": "stable_int('budget-bootstrap', contrast)",
            "percentiles": [2.5, 97.5],
            "fallback": ("a contrast whose stratum holds fewer than 2 runs is resampled as "
                         "one pool (never a crash)"),
        },
        "primary": primary,
        "v1": v1,
        "v2": v2,
        "decision": dec,
        "secondary": secondary,
        "strata": strata_contrasts(runs, b=b),
        "c_vs_a": c_vs_a(runs, b=b),
        "report_only": report_only_metrics(runs, contrasts),
        "per_seed": primary["per_seed"],
        "arms": table,
        "observed": observed_rates(runs),
        "saturation": saturation,
    }


__all__ = [
    "BOOTSTRAP_B",
    "C_HARM",
    "DELTA_SR",
    "DELTA_TOK",
    "LABEL_R1A",
    "LABEL_R1B",
    "LABEL_R2",
    "LABEL_R3_PREFIX",
    "LABEL_R4",
    "LABEL_REPORTED",
    "LABEL_VOID",
    "RULE_NO_VERDICT",
    "SATURATION_BOUNDS",
    "SR_HALF_WIDTH_PLAN",
    "TOK_HALF_WIDTH_FLAG",
    "analyse",
    "arm_table",
    "contrast",
    "c_vs_a",
    "decide",
    "normalise_runs",
    "observed_rates",
    "paired_bootstrap",
    "per_seed_estimates",
    "report_only_metrics",
    "rule_of",
    "saturation_check",
    "strata_contrasts",
    "secondary_contrasts",
    "v1_check",
    "v2_check",
]
