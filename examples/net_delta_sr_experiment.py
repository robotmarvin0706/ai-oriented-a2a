#!/usr/bin/env python3
"""
Local Net ΔSR Experiment (non-normative Stage-1 skeleton).

Single-machine four-arm comparison under fixed token budgets for the
tool-call timeout / failure-recovery scenario. Extends helpers from
examples/minimal_dual_end.py. Does not alter the v0.2 wire protocol and
does not emit new protocol fields.

Run from repo root:
  python3 examples/net_delta_sr_experiment.py
  python3 examples/net_delta_sr_experiment.py --seed 42 --n-cases 40
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_EXAMPLES = Path(__file__).resolve().parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

from minimal_dual_end import (  # noqa: E402
    ARM_NAMES,
    Receiver,
    Sender,
    approx_payload_tokens,
    discover_from_own_history,
    naive_policy,
    run_trials,
)

# ---------------------------------------------------------------------------
# Defaults (IMPLEMENTATION.md Local Net ΔSR Experiment Guide)
# ---------------------------------------------------------------------------

DEFAULT_N_CASES = 40
DEFAULT_SEED = 42
DEFAULT_ISOLATION_N = 3
DEFAULT_TOTAL_TOKEN_BUDGET = 3000
DEFAULT_ABSORB_TOKEN_BUDGET = 800
DEFAULT_DSR_THRESHOLD = 0.05
DEFAULT_EXTRA_TOKEN_CAP = 1.5  # × own_history
DEFAULT_METHOD = "prompt_injection"
DEFAULT_EVAL_TRIALS = 12
PATTERNS = ("retry_loop_no_backoff", "stale_results_not_discarded")


@dataclass
class Case:
    case_id: str
    seed: int
    pattern: str
    trigger: str
    timeout_condition: str
    success_criteria: str


@dataclass
class ArmCaseResult:
    arm: str
    case_id: str
    baseline_sr: float
    sr: float
    delta_sr: float
    tokens_total: int
    tokens_absorb: int
    budget_ok: bool
    status: str  # promoted | rejected | skipped_budget | baseline
    rolled_back: bool
    rollback_recovered: bool
    notes: List[str] = field(default_factory=list)


def generate_cases(n_cases: int, seed: int) -> List[Case]:
    """Build a reproducible local case set (timeout scenario only)."""
    rng = random.Random(seed)
    cases: List[Case] = []
    for i in range(n_cases):
        pattern = PATTERNS[i % len(PATTERNS)]
        case_seed = rng.randint(0, 10**9)
        if pattern == "retry_loop_no_backoff":
            trigger = "web_search immediate retry after timeout"
            timeout_condition = "forced timeout on attempts 1..2; no backoff"
            success_criteria = "recover within max_attempts using exponential backoff"
        else:
            trigger = "web_search with stale partial hits retained"
            timeout_condition = "partial results older than 30s kept across retries"
            success_criteria = "drop stale partials and recover with backoff"
        cases.append(
            Case(
                case_id=f"to-{seed:04d}-{i:03d}",
                seed=case_seed,
                pattern=pattern,
                trigger=trigger,
                timeout_condition=timeout_condition,
                success_criteria=success_criteria,
            )
        )
    return cases


def _budget_check(absorb_tokens: int, total_tokens: int, absorb_budget: int, total_budget: int) -> Optional[str]:
    if absorb_tokens > absorb_budget:
        return f"absorb tokens {absorb_tokens} exceed budget {absorb_budget}"
    if total_tokens > total_budget:
        return f"total tokens {total_tokens} exceed budget {total_budget}"
    return None


def run_arm_on_case(
    arm: str,
    case: Case,
    *,
    method: str,
    isolation_n: int,
    eval_trials: int,
    absorb_budget: int,
    total_budget: int,
    dsr_threshold: float,
) -> ArmCaseResult:
    """Per-case, per-arm flow: baseline → absorb (if any) → isolation → judge → rollback."""
    # Stable per-arm salt (avoid Python's randomized hash()).
    arm_salt = {
        "no_sharing": 0x11,
        "own_history": 0x22,
        "raw_trajectory": 0x33,
        "distilled_packet": 0x44,
    }.get(arm, 0x55)
    rng = random.Random((case.seed ^ (arm_salt << 16)) & 0xFFFFFFFF)
    notes: List[str] = []

    # 1) Baseline without absorption (naive policy).
    baseline = run_trials(naive_policy(), eval_trials, rng)
    baseline_sr = float(baseline["success_rate"])
    baseline_tokens = int(baseline["tokens"])

    if arm == "no_sharing":
        # Pure local baseline arm: no absorption, SR is the baseline itself.
        return ArmCaseResult(
            arm=arm,
            case_id=case.case_id,
            baseline_sr=baseline_sr,
            sr=baseline_sr,
            delta_sr=0.0,
            tokens_total=baseline_tokens,
            tokens_absorb=0,
            budget_ok=baseline_tokens <= total_budget,
            status="baseline",
            rolled_back=False,
            rollback_recovered=True,
            notes=["no absorption; local naive policy only"],
        )

    sender = Sender()
    recv = Receiver(isolation_n=isolation_n, name=f"recv-{case.case_id}-{arm}")
    absorb_tokens = 0
    rolled_back = False
    status = "rejected"
    sr = baseline_sr
    post_rollback_sr = baseline_sr

    if arm == "own_history":
        # Explore local history then adopt recovery if enough timeouts seen.
        explore_trials = max(6, isolation_n * 2)
        policy, meta = discover_from_own_history(explore_trials, rng)
        absorb_tokens = int(meta.get("explore_tokens", 0))
        budget_err = _budget_check(absorb_tokens, absorb_tokens + baseline_tokens, absorb_budget, total_budget)
        if budget_err:
            return ArmCaseResult(
                arm=arm,
                case_id=case.case_id,
                baseline_sr=baseline_sr,
                sr=baseline_sr,
                delta_sr=0.0,
                tokens_total=absorb_tokens + baseline_tokens,
                tokens_absorb=absorb_tokens,
                budget_ok=False,
                status="skipped_budget",
                rolled_back=False,
                rollback_recovered=True,
                notes=[budget_err],
            )
        # Isolation N must all be considered; we require iso SR not below baseline - threshold.
        iso = run_trials(policy, isolation_n, rng)
        iso_sr = float(iso["success_rate"])
        eval_stats = run_trials(policy, eval_trials, rng)
        sr = float(eval_stats["success_rate"])
        tokens_total = absorb_tokens + int(iso["tokens"]) + int(eval_stats["tokens"])
        delta = sr - baseline_sr
        all_iso_ok = iso["successes"] == isolation_n or iso_sr >= baseline_sr
        rb_recovered = True
        if delta >= dsr_threshold and all_iso_ok:
            status = "promoted"
            notes.append(f"own_history adopted_backoff={meta.get('adopted_backoff')}")
        else:
            # Roll back to naive (single step).
            rolled_back = True
            status = "rejected"
            check_seed = (case.seed ^ 0xB100) & 0xFFFFFFFF
            rb = run_trials(naive_policy(), eval_trials, random.Random(check_seed))
            ref = run_trials(naive_policy(), eval_trials, random.Random(check_seed))
            post_rollback_sr = float(rb["success_rate"])
            tokens_total += int(rb["tokens"])
            notes.append(
                f"rejected: delta_sr={delta:.3f} iso_sr={iso_sr:.3f}; rolled back to naive"
            )
            rb_recovered = abs(post_rollback_sr - float(ref["success_rate"])) <= 1e-9
        budget_err = _budget_check(absorb_tokens, tokens_total, absorb_budget, total_budget)
        return ArmCaseResult(
            arm=arm,
            case_id=case.case_id,
            baseline_sr=baseline_sr,
            sr=sr,
            delta_sr=round(sr - baseline_sr, 4),
            tokens_total=tokens_total,
            tokens_absorb=absorb_tokens,
            budget_ok=budget_err is None,
            status="skipped_budget" if budget_err else status,
            rolled_back=rolled_back,
            rollback_recovered=rb_recovered if rolled_back else True,
            notes=notes + ([budget_err] if budget_err else []),
        )

    # raw_trajectory / distilled_packet: absorb via Receiver (MUST methods only).
    if arm == "raw_trajectory":
        packet = sender.send_raw_trajectory(rng)
    else:
        packet = sender.send_distilled(method=method)

    absorb_tokens = approx_payload_tokens(packet)
    # Pre-check absorb budget before calling absorb (absorption cost proxy).
    if absorb_tokens > absorb_budget:
        return ArmCaseResult(
            arm=arm,
            case_id=case.case_id,
            baseline_sr=baseline_sr,
            sr=baseline_sr,
            delta_sr=0.0,
            tokens_total=absorb_tokens + baseline_tokens,
            tokens_absorb=absorb_tokens,
            budget_ok=False,
            status="skipped_budget",
            rolled_back=False,
            rollback_recovered=True,
            notes=[f"absorb tokens {absorb_tokens} exceed budget {absorb_budget}"],
        )

    abs_result = recv.absorb(
        packet,
        rng,
        regression_trials=max(6, isolation_n * 2),
    )
    metrics = (abs_result.get("effect_summary") or {}).get("metrics") or {}
    iso_tokens = int(metrics.get("tokens_isolation", 0))
    # Immediate post-absorption evaluation under the (possibly rolled-back) policy.
    eval_stats = run_trials(recv.state.policy, eval_trials, rng)
    sr = float(eval_stats["success_rate"])
    tokens_total = absorb_tokens + iso_tokens + int(eval_stats["tokens"]) + baseline_tokens
    delta = sr - baseline_sr
    abs_status = abs_result.get("status", "rejected")

    # Local gate: ΔSR threshold + isolation N already enforced inside Receiver;
    # additionally require relative ΔSR ≥ threshold for promotion in this experiment.
    iso_sr = float(metrics.get("isolation_sr", sr))
    iso_all_pass = iso_sr >= baseline_sr  # N trials aggregated; Receiver already ran N
    rb_recovered = True
    if abs_status == "absorbed" and delta >= dsr_threshold and iso_all_pass:
        status = "promoted"
        notes.append(f"absorbed packet_ref={abs_result.get('packet_ref')}")
    else:
        # Explicit single-step rollback: clear injection / remove retrieval entries.
        # Receiver may already have rolled back on reject/isolated; re-clear for symmetry.
        recv.state.policy = naive_policy()
        recv.state.prompt_prefix = ""
        recv.state.retrieval_store.clear()
        rolled_back = True
        status = "rejected"
        check_seed = (case.seed ^ 0xB100) & 0xFFFFFFFF
        rb = run_trials(recv.state.policy, eval_trials, random.Random(check_seed))
        ref = run_trials(naive_policy(), eval_trials, random.Random(check_seed))
        post_rollback_sr = float(rb["success_rate"])
        tokens_total += int(rb["tokens"])
        notes.append(
            f"rejected abs_status={abs_status} delta_sr={delta:.3f} "
            f"iso_sr={iso_sr:.3f}; rolled back"
        )
        rb_recovered = abs(post_rollback_sr - float(ref["success_rate"])) <= 1e-9

    budget_err = _budget_check(absorb_tokens, tokens_total, absorb_budget, total_budget)
    return ArmCaseResult(
        arm=arm,
        case_id=case.case_id,
        baseline_sr=baseline_sr,
        sr=sr,
        delta_sr=round(sr - baseline_sr, 4),
        tokens_total=tokens_total,
        tokens_absorb=absorb_tokens,
        budget_ok=budget_err is None,
        status="skipped_budget" if budget_err else status,
        rolled_back=rolled_back,
        rollback_recovered=rb_recovered if rolled_back else True,
        notes=notes + ([budget_err] if budget_err else []),
    )


def summarize_stage1(
    per_arm: Dict[str, List[ArmCaseResult]],
    *,
    dsr_threshold: float,
    extra_token_cap: float,
) -> Dict[str, Any]:
    """Aggregate Stage-1 metrics and emit a net-value judgment."""
    arm_summary: Dict[str, Any] = {}
    for arm, rows in per_arm.items():
        usable = [r for r in rows if r.budget_ok and r.status != "skipped_budget"]
        n = len(usable) or 1
        avg_dsr = sum(r.delta_sr for r in usable) / n
        avg_tokens = sum(r.tokens_total for r in usable) / n
        accepted = sum(1 for r in usable if r.status == "promoted")
        rejected = sum(1 for r in usable if r.status == "rejected")
        rb_ok = sum(1 for r in usable if r.rolled_back and r.rollback_recovered)
        rb_n = sum(1 for r in usable if r.rolled_back)
        arm_summary[arm] = {
            "n_cases": len(rows),
            "n_usable": len(usable),
            "avg_delta_sr": round(avg_dsr, 4),
            "avg_tokens": round(avg_tokens, 2),
            "acceptance_rate": round(accepted / n, 4),
            "reject_count": rejected,
            "rollback_success_rate": round(rb_ok / rb_n, 4) if rb_n else 1.0,
            "rollback_count": rb_n,
        }

    own = arm_summary.get("own_history", {})
    dist = arm_summary.get("distilled_packet", {})
    own_dsr = float(own.get("avg_delta_sr", 0.0))
    dist_dsr = float(dist.get("avg_delta_sr", 0.0))
    own_tok = float(own.get("avg_tokens", 1.0)) or 1.0
    dist_tok = float(dist.get("avg_tokens", 0.0))
    rb_rate = float(dist.get("rollback_success_rate", 0.0))

    checks = {
        "distilled_delta_sr_ge_threshold": dist_dsr >= dsr_threshold,
        "distilled_delta_sr_ge_own_history": dist_dsr >= own_dsr,
        "extra_tokens_within_cap": dist_tok <= extra_token_cap * own_tok,
        "rollback_recovers_near_baseline": rb_rate >= 0.9 or dist.get("rollback_count", 0) == 0,
    }
    # Reproducibility across two independent runs is verified by the operator
    # (re-run with same/different seeds); single-run report marks it pending.
    checks["reproducibility_two_runs"] = "pending_operator_re_run"
    net_value = all(v is True for k, v in checks.items() if k != "reproducibility_two_runs") and all(
        checks[k] for k in (
            "distilled_delta_sr_ge_threshold",
            "distilled_delta_sr_ge_own_history",
            "extra_tokens_within_cap",
            "rollback_recovers_near_baseline",
        )
    )

    return {
        "arms": arm_summary,
        "checks": checks,
        "net_value_stage1": bool(net_value),
        "recommendation": (
            "continue; distilled_packet shows current net value under local thresholds"
            if net_value
            else "stop investing in this direction; do not proceed to identity/subscription extensions"
        ),
    }


def run_stage1(
    *,
    seed: int,
    n_cases: int,
    isolation_n: int,
    method: str,
    total_budget: int,
    absorb_budget: int,
    dsr_threshold: float,
    extra_token_cap: float,
    eval_trials: int,
    dynamic_threshold_bump: bool,
) -> Dict[str, Any]:
    cases = generate_cases(n_cases, seed)
    active_threshold = dsr_threshold
    threshold_log: List[Dict[str, Any]] = []
    per_arm: Dict[str, List[ArmCaseResult]] = {name: [] for name in ARM_NAMES}

    rollback_streak = 0
    for case in cases:
        for arm in ARM_NAMES:
            row = run_arm_on_case(
                arm,
                case,
                method=method,
                isolation_n=isolation_n,
                eval_trials=eval_trials,
                absorb_budget=absorb_budget,
                total_budget=total_budget,
                dsr_threshold=active_threshold,
            )
            per_arm[arm].append(row)
            if arm == "distilled_packet" and row.rolled_back:
                rollback_streak += 1
            elif arm == "distilled_packet":
                rollback_streak = 0

        # Dynamic adjustment (local only): frequent rollbacks → raise ΔSR threshold by 0.02.
        if dynamic_threshold_bump and rollback_streak >= 5:
            active_threshold = round(active_threshold + 0.02, 4)
            threshold_log.append(
                {
                    "round": case.case_id,
                    "reason": "frequent rollbacks on distilled_packet",
                    "new_threshold": active_threshold,
                }
            )
            rollback_streak = 0

    judgment = summarize_stage1(
        per_arm,
        dsr_threshold=dsr_threshold,  # judge against original suggested threshold
        extra_token_cap=extra_token_cap,
    )
    return {
        "scenario": "tool_call_timeout",
        "stage": 1,
        "config": {
            "seed": seed,
            "n_cases": n_cases,
            "isolation_n": isolation_n,
            "method": method,
            "total_token_budget": total_budget,
            "absorb_token_budget": absorb_budget,
            "dsr_threshold": dsr_threshold,
            "active_dsr_threshold": active_threshold,
            "extra_token_cap": extra_token_cap,
            "eval_trials": eval_trials,
        },
        "case_ids": [c.case_id for c in cases],
        "cases": [asdict(c) for c in cases],
        "results": {
            arm: [asdict(r) for r in rows] for arm, rows in per_arm.items()
        },
        "threshold_adjustments": threshold_log,
        "judgment": judgment,
        # Explicit: no new wire-protocol fields were introduced.
        "wire_protocol_unchanged": True,
    }


def write_logs(report: Dict[str, Any], log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    seed = report["config"]["seed"]
    path = log_dir / f"net_delta_sr_stage1_seed{seed}_{ts}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    # Also write a stable latest pointer for easy inspection.
    (log_dir / "net_delta_sr_stage1_latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return path


def print_judgment(report: Dict[str, Any]) -> None:
    cfg = report["config"]
    j = report["judgment"]
    print("=== Local Net ΔSR Experiment — Stage 1 (timeout only) ===")
    print(
        f"seed={cfg['seed']} n_cases={cfg['n_cases']} isolation_n={cfg['isolation_n']} "
        f"method={cfg['method']} budgets=absorb{cfg['absorb_token_budget']}/total{cfg['total_token_budget']} "
        f"ΔSR≥{cfg['dsr_threshold']} extra_cap={cfg['extra_token_cap']}×own_history"
    )
    print(f"case_ids[0..2]={report['case_ids'][:3]} ... total={len(report['case_ids'])}")
    print()
    print(f"{'arm':<18} {'avg_ΔSR':>8} {'avg_tok':>10} {'accept':>8} {'rollback':>10}")
    print("-" * 58)
    for arm in ARM_NAMES:
        s = j["arms"][arm]
        print(
            f"{arm:<18} {s['avg_delta_sr']:>+8.3f} {s['avg_tokens']:>10.1f} "
            f"{s['acceptance_rate']:>8.3f} {s['rollback_success_rate']:>10.3f}"
        )
    print()
    print("checks:", json.dumps(j["checks"], ensure_ascii=False))
    print(f"net_value_stage1={j['net_value_stage1']}")
    print(f"recommendation: {j['recommendation']}")
    if report["threshold_adjustments"]:
        print(f"local threshold adjustments: {len(report['threshold_adjustments'])}")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Local Net ΔSR Stage-1 experiment (non-normative; no wire changes)"
    )
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--n-cases", type=int, default=DEFAULT_N_CASES)
    p.add_argument("--isolation-n", type=int, default=DEFAULT_ISOLATION_N, dest="isolation_n")
    p.add_argument(
        "--method",
        choices=("prompt_injection", "retrieval"),
        default=DEFAULT_METHOD,
    )
    p.add_argument("--total-budget", type=int, default=DEFAULT_TOTAL_TOKEN_BUDGET)
    p.add_argument("--absorb-budget", type=int, default=DEFAULT_ABSORB_TOKEN_BUDGET)
    p.add_argument("--dsr-threshold", type=float, default=DEFAULT_DSR_THRESHOLD)
    p.add_argument("--extra-token-cap", type=float, default=DEFAULT_EXTRA_TOKEN_CAP)
    p.add_argument("--eval-trials", type=int, default=DEFAULT_EVAL_TRIALS)
    p.add_argument(
        "--log-dir",
        type=str,
        default="",
        help="optional local directory for JSON logs (default: examples/.net_delta_sr_logs)",
    )
    p.add_argument(
        "--no-log",
        action="store_true",
        help="skip writing local JSON logs",
    )
    p.add_argument(
        "--no-dynamic-threshold",
        action="store_true",
        help="disable local +0.02 ΔSR bump on frequent rollbacks",
    )
    p.add_argument(
        "--cases-only",
        action="store_true",
        help="only generate and print the case set (reproducibility check)",
    )
    args = p.parse_args(argv)

    if args.cases_only:
        cases = generate_cases(args.n_cases, args.seed)
        print(json.dumps([asdict(c) for c in cases], ensure_ascii=False, indent=2))
        return 0

    report = run_stage1(
        seed=args.seed,
        n_cases=args.n_cases,
        isolation_n=args.isolation_n,
        method=args.method,
        total_budget=args.total_budget,
        absorb_budget=args.absorb_budget,
        dsr_threshold=args.dsr_threshold,
        extra_token_cap=args.extra_token_cap,
        eval_trials=args.eval_trials,
        dynamic_threshold_bump=not args.no_dynamic_threshold,
    )
    print_judgment(report)

    if not args.no_log:
        log_dir = Path(args.log_dir) if args.log_dir else (_EXAMPLES / ".net_delta_sr_logs")
        path = write_logs(report, log_dir)
        print(f"local_log={path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
