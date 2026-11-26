"""Analysis and decision rules of the selection experiment (PREREG_SELECTION §8-§12, §14).

The unit of analysis is the run. Contrasts are paired per run; CIs are a
percentile bootstrap over runs, stratified by seed (runs resampled within each
seed), B = 2000, seeded with ``stable_int("selection-bootstrap", host, contrast)``.
Point estimates and bootstrap means are computed on integer numerators, so the
comparisons with 0 and with delta are exact.
"""

from __future__ import annotations

import random
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

from experiments.timeout_recovery.host import stable_int
from experiments.timeout_recovery.run import BOOTSTRAP_B

from .sim import ARM_ORDER, ARMS, DELTA, HOSTS, PRIMARY_HOST

# Verdict labels, verbatim from §12.
LABEL_VOID = "VOID"
LABEL_R1A = "supported: envelope-informed selection improves plateau SR by at least the margin"
LABEL_R1B = "positive but below the 0.10 margin; not practically significant"
LABEL_R3 = "envelope-informed selection is worse"
LABEL_R2 = "equivalent within ±0.10 ΔSR"
LABEL_R4 = "undetermined"

#: Planned half-width at the pessimistic G = 0.65 (§10).
HALF_WIDTH_FLAG = 0.065

#: (name, left arm, right arm, role) for plateau_SR contrasts.
SR_CONTRASTS: Tuple[Tuple[str, str, str, str], ...] = (
    ("D1-C", "D1", "C", "primary"),
    ("D2-C", "D2", "C", "secondary"),
    ("C-V1", "C", "V1", "validity V1"),
    ("V2-C", "V2", "C", "validity V2"),
    ("B-A", "B", "A", "secondary (content value)"),
    ("C-B", "C", "B", "secondary (identical SR by design)"),
)
#: Contrasts between selection rules: the only ones the §12 labels are worded for.
SELECTION_CONTRASTS = ("D1-C", "D2-C")
#: Token contrasts (C - B is the packaging cost, §4.1).
TOKEN_CONTRASTS: Tuple[Tuple[str, str, str], ...] = (
    ("C-B", "C", "B"),
    ("D1-C", "D1", "C"),
    ("D2-C", "D2", "C"),
)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def stratified_bootstrap(strata: Dict[int, List[int]], den: int, seed_parts: Tuple[Any, ...],
                         b: int = BOOTSTRAP_B) -> Tuple[Fraction, Fraction, Fraction]:
    """Mean and 95% percentile CI of integer values / den, resampling within strata.

    Same percentile indices as ``timeout_recovery.run.bootstrap_ci``.
    Returns exact Fractions (estimate, lo, hi).
    """
    keys = sorted(strata)
    n = sum(len(strata[k]) for k in keys)
    if n == 0:
        z = Fraction(0)
        return z, z, z
    total = sum(sum(strata[k]) for k in keys)
    rng = random.Random(stable_int("selection-bootstrap", *seed_parts))
    sums = []
    for _ in range(b):
        s = 0
        for k in keys:
            vals = strata[k]
            m = len(vals)
            s += sum(vals[rng.randrange(m)] for _ in range(m))
        sums.append(s)
    sums.sort()
    lo = Fraction(sums[int(0.025 * b)], n * den)
    hi = Fraction(sums[int(0.975 * b) - 1], n * den)
    return Fraction(total, n * den), lo, hi


def paired_sr_values(runs: Sequence[Dict[str, Any]], host: str, left: str, right: str) -> Tuple[Dict[int, List[int]], int]:
    """Per-seed lists of paired plateau_SR differences as integer numerators."""
    strata: Dict[int, List[int]] = {}
    den = None
    for r in runs:
        a, b = r["hosts"][host][left], r["hosts"][host][right]
        assert a["plateau_den"] == b["plateau_den"]
        den = a["plateau_den"]
        strata.setdefault(r["seed"], []).append(a["plateau_num"] - b["plateau_num"])
    return strata, den or 1


def contrast(runs: Sequence[Dict[str, Any]], host: str, name: str, left: str, right: str) -> Dict[str, Any]:
    strata, den = paired_sr_values(runs, host, left, right)
    est, lo, hi = stratified_bootstrap(strata, den, (host, name))
    per_seed = {str(s): float(Fraction(sum(v), len(v) * den)) for s, v in strata.items()}
    return {"contrast": name, "host": host, "estimate": est, "lo": lo, "hi": hi,
            "half_width": (hi - lo) / 2, "per_seed": per_seed, "runs": sum(len(v) for v in strata.values())}


def token_contrast(runs: Sequence[Dict[str, Any]], host: str, name: str, left: str, right: str,
                   field: str) -> Dict[str, Any]:
    strata: Dict[int, List[int]] = {}
    for r in runs:
        a, b = r["hosts"][host][left], r["hosts"][host][right]
        strata.setdefault(r["seed"], []).append(a[field] - b[field])
    est, lo, hi = stratified_bootstrap(strata, 1, (host, f"{name}:{field}"))
    return {"contrast": name, "host": host, "field": field, "estimate": est, "lo": lo, "hi": hi}


# ---------------------------------------------------------------------------
# Rules (§11, §12)
# ---------------------------------------------------------------------------


def rule_of(est: Fraction, lo: Fraction, hi: Fraction, delta: Fraction = DELTA) -> Tuple[str, str]:
    """R1a, R1b, R3, R2, R4 in the registered order (the V row is separate)."""
    if lo > 0 and est >= delta:
        return "R1a", LABEL_R1A
    if lo > 0:
        return "R1b", LABEL_R1B
    if hi < 0:
        return "R3", LABEL_R3
    if lo >= -delta and hi <= delta:
        return "R2", LABEL_R2
    return "R4", LABEL_R4


def v1_outcome(c_minus_v1: Dict[str, Any]) -> str:
    """§11.1: pass iff the CI of C - V1 has lower bound > 0."""
    return "pass" if c_minus_v1["lo"] > 0 else "fail"


def v2_outcome(v2_minus_c: Dict[str, Any], delta: Fraction = DELTA) -> str:
    """§11.2: pass / VOID / inconclusive."""
    lo, hi = v2_minus_c["lo"], v2_minus_c["hi"]
    if lo > 0:
        return "VOID"
    if lo <= 0 <= hi and lo >= -delta and hi <= delta:
        return "pass"
    return "inconclusive"


def verdict(main: Dict[str, Any], v1: str, v2: str) -> Dict[str, Any]:
    """Apply §12 in order. The headline label is what may be stated."""
    rule, label = rule_of(main["estimate"], main["lo"], main["hi"])
    if v1 == "fail" or v2 == "VOID":
        reasons = []
        if v1 == "fail":
            reasons.append("V1 failed: the CI of C - V1 does not have lower bound > 0")
        if v2 == "VOID":
            reasons.append("V2 is VOID: shuffled envelopes still beat random selection (CI of V2 - C above 0)")
        return {"rule": "V", "label": LABEL_VOID, "headline": LABEL_VOID, "reason": "; ".join(reasons),
                "information_rule": rule, "information_label": label}
    if v2 == "inconclusive":
        return {"rule": rule, "label": label, "headline": LABEL_R4,
                "reason": "V2 inconclusive: the headline verdict is capped at undetermined",
                "information_rule": rule, "information_label": label}
    return {"rule": rule, "label": label, "headline": label, "reason": "", "information_rule": rule,
            "information_label": label}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def arm_summary(runs: Sequence[Dict[str, Any]], host: str, label: str) -> Dict[str, Any]:
    rows = [r["hosts"][host][label] for r in runs]
    n = len(rows)
    absorption: Dict[str, Dict[str, Any]] = {}
    for fam in ("effective", "mediocre", "harmful"):
        absorption[fam] = {}
        for k in ("selected", "absorbed", "rolled_back"):
            tot = sum(r["absorption"][fam][k] for r in rows)
            absorption[fam][k] = {"total": tot, "per_run": tot / n if n else None}
    found = [r["slots_before_first_effective"] for r in rows if r["slots_before_first_effective"] is not None]
    changes = sum(r["policy_changes"] for r in rows)
    attributed = sum(r["attributed_changes"] for r in rows)
    per_seed: Dict[str, List[float]] = {}
    for r_run, row in zip(runs, rows):
        per_seed.setdefault(str(r_run["seed"]), []).append(row["plateau_sr"])
    return {
        "arm": label,
        "name": ARMS[label][0],
        "runs": n,
        "plateau_sr": _mean([r["plateau_sr"] for r in rows]),
        "plateau_sr_per_seed": {s: _mean(v) for s, v in per_seed.items()},
        "auc": _mean([r["auc"] for r in rows]),
        "tasks_to_plateau": _mean([r["tasks_to_plateau"] for r in rows]),
        "plateau_not_reached": sum(1 for r in rows if r["plateau_not_reached"]),
        "tokens_to_plateau": _mean([r["tokens_to_plateau"] for r in rows]),
        "total_tokens": _mean([r["total_tokens"] for r in rows]),
        "selection_tokens": _mean([r["selection_tokens"] for r in rows]),
        "slot_tokens": _mean([r["slot_tokens"] for r in rows]),
        "absorption": absorption,
        "runs_with_effective_absorbed": len(found),
        "slots_before_first_effective_mean": _mean([float(x) for x in found]),
        "policy_changes": changes,
        "attributed_changes": attributed,
        "attribution_rate": (attributed / changes) if changes else None,
        "content_policy_rejections": sum(r["content_policy_rejections"] for r in rows),
        "rollbacks": sum(r["rollbacks"] for r in rows),
        "rollback_success_rate": (sum(r["rollbacks_ok"] for r in rows) / sum(r["rollbacks"] for r in rows))
        if sum(r["rollbacks"] for r in rows) else None,
    }


def analyse(runs: Sequence[Dict[str, Any]], hosts: Sequence[str] = HOSTS) -> Dict[str, Any]:
    """Per-host contrasts and verdicts.

    V1 and V2 are registered on the primary contrast on ``naive_host`` (§11);
    their outcomes gate every table, including the secondary ones on
    ``working_host``. The same checks computed on ``working_host`` are
    reported as information only.
    """
    out: Dict[str, Any] = {"hosts": {}}
    per_host: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for host in hosts:
        contrasts = {name: contrast(runs, host, name, a, b) for name, a, b, _role in SR_CONTRASTS}
        for name, _a, _b, role in SR_CONTRASTS:
            contrasts[name]["role"] = role
        per_host[host] = contrasts
    gate_host = PRIMARY_HOST if PRIMARY_HOST in per_host else hosts[0]
    v1 = v1_outcome(per_host[gate_host]["C-V1"])
    v2 = v2_outcome(per_host[gate_host]["V2-C"])
    for host in hosts:
        contrasts = per_host[host]
        tokens = {f"{name}:{field}": token_contrast(runs, host, name, a, b, field)
                  for name, a, b in TOKEN_CONTRASTS for field in ("total_tokens", "tokens_to_plateau")}
        out["hosts"][host] = {
            "arms": {label: arm_summary(runs, host, label) for label in ARM_ORDER},
            "contrasts": contrasts,
            "token_contrasts": tokens,
            "v1_on_this_host": v1_outcome(contrasts["C-V1"]),
            "v2_on_this_host": v2_outcome(contrasts["V2-C"]),
            "verdicts": {name: verdict(contrasts[name], v1, v2) for name in SELECTION_CONTRASTS},
        }
    p = out["hosts"].get(PRIMARY_HOST)
    if p is not None:
        hw = p["contrasts"]["D1-C"]["half_width"]
        out["headline"] = {
            "host": PRIMARY_HOST,
            "contrast": "D1-C",
            "verdict": p["verdicts"]["D1-C"],
            "v1": v1,
            "v2": v2,
            "half_width": hw,
            "noisier_than_planned": float(hw) > HALF_WIDTH_FLAG,
        }
    return out


def jsonable(obj: Any) -> Any:
    """Fractions to rounded floats (4 dp), recursively."""
    if isinstance(obj, Fraction):
        return round(float(obj) + 0.0, 4)
    if isinstance(obj, float):
        return round(obj + 0.0, 4)
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj
