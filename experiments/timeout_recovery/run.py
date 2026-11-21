"""Four-arm timeout-recovery experiment runner (non-normative).

    python -m experiments.timeout_recovery.run --seeds 42 7 2026 --n 30

Writes raw trajectories (v0.4 Experience Packets, one per line) to
``datasets/timeout_recovery/runs/<seed>/``, per-seed aggregates to
``experiments/timeout_recovery/summaries/seed-<seed>.json`` (kept out of
``datasets/``, whose ``*.json`` files must be protocol messages) and the table
to ``experiments/timeout_recovery/RESULTS.md``. Output is byte-identical for the
same arguments: no wall-clock time is written, ids and draws come from sha256.

Decision rules are fixed here (``judge_seed`` / ``judge_overall``) and are
printed as-is, including a negative verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .host import (
    ABSORB_TOKEN_BUDGET,
    DSR_THRESHOLD,
    EXTRA_COST_CAP,
    ISOLATION_N,
    MODEL_NAME,
    TOTAL_TOKEN_BUDGET,
    WINDOW_EPISODES,
    Case,
    Episode,
    Host,
    LLMStrategyModel,
    RuleStrategyModel,
    approx_tokens,
    learn_from_own_history,
    run_window,
    stable_int,
)
from .packets import PEER_POLICY, control_packets, distilled_packet, raw_trajectory_packet

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "datasets" / "timeout_recovery" / "runs"
DEFAULT_RESULTS = Path(__file__).resolve().parent / "RESULTS.md"
DEFAULT_SUMMARY_DIR = Path(__file__).resolve().parent / "summaries"
ARMS = ("no_sharing", "own_history", "raw_trajectory", "distilled_packet")
CONTROLS = ("wrong_packet", "empty_packet", "irrelevant_packet", "injection_packet")
BOOTSTRAP_B = 2000
CI_METHOD = f"paired bootstrap over cases, percentile 95% CI, B={BOOTSTRAP_B}"
SCENARIO_VERSION = "sim-v1"


@dataclass
class ArmCase:
    """One arm on one case."""

    arm: str
    case: Case
    eval_episodes: List[Episode]
    overhead_tokens: int  # exploration / absorption / isolation, before evaluation
    overhead_tool_calls: int
    status: Optional[str]  # absorbed / rejected / None (no candidate change)
    reason: str = ""
    policy: str = ""
    rollback_triggered: bool = False
    rollback_ok: Optional[bool] = None
    isolation_dsr: Optional[float] = None
    strategy_parsed: Optional[bool] = None
    model_calls: int = 0
    reply: Optional[Dict[str, Any]] = None

    @property
    def successes(self) -> List[bool]:
        return [e.success for e in self.eval_episodes]

    @property
    def sr(self) -> float:
        return sum(self.successes) / len(self.eval_episodes)

    def window_sr(self, window: str) -> float:
        eps = [e for e in self.eval_episodes if e.window == window]
        return sum(1 for e in eps if e.success) / len(eps)

    @property
    def tokens(self) -> int:
        return self.overhead_tokens + sum(e.tokens for e in self.eval_episodes)

    @property
    def tool_calls(self) -> int:
        return self.overhead_tool_calls + sum(e.tool_calls for e in self.eval_episodes)


def _evaluate(host: Host) -> List[Episode]:
    return run_window(host.policy, host.case, "immediate") + run_window(host.policy, host.case, "delayed")


def _from_packet(arm: str, case: Case, packet: Dict[str, Any], model_factory: Callable[[], Any]) -> ArmCase:
    host = Host(case, model_factory())
    reply = host.handle_message(packet)
    rec = host.log[-1] if host.log else None
    metrics = (reply.get("effect_summary") or {}).get("metrics") or {}
    return ArmCase(
        arm=arm,
        case=case,
        eval_episodes=_evaluate(host),
        overhead_tokens=(rec.absorb_tokens + rec.isolation_tokens) if rec else 0,
        overhead_tool_calls=rec.isolation_tool_calls if rec else 0,
        status=reply.get("status"),
        reason=reply.get("reason", ""),
        policy=host.policy.label(),
        rollback_triggered=bool(rec and rec.rollback_triggered),
        rollback_ok=rec.rollback_ok if rec else None,
        isolation_dsr=rec.isolation_dsr if rec and rec.params else None,
        strategy_parsed=bool(metrics.get("strategy_parsed")) if metrics else False,
        model_calls=rec.model_calls if rec else 0,
        reply=reply,
    )


def run_case(case: Case, model_factory: Callable[[], Any]) -> Dict[str, ArmCase]:
    out: Dict[str, ArmCase] = {}

    host = Host(case, model_factory())
    out["no_sharing"] = ArmCase("no_sharing", case, _evaluate(host), 0, 0, None, policy=host.policy.label())

    host = Host(case, model_factory())
    explore = run_window(host.policy, case, "explore")
    params, evidence = learn_from_own_history(explore)
    explore_tokens = sum(e.tokens for e in explore)
    explore_calls = sum(e.tool_calls for e in explore)
    if params:
        note_tokens = approx_tokens(params)  # the learned rule kept in context
        rec = host.gate("own_history", params, evidence, 0, context_tokens=note_tokens)
        out["own_history"] = ArmCase(
            "own_history", case, _evaluate(host),
            explore_tokens + rec.isolation_tokens, explore_calls + rec.isolation_tool_calls,
            rec.status, rec.reason, host.policy.label(), rec.rollback_triggered, rec.rollback_ok,
            rec.isolation_dsr, True,
        )
    else:
        out["own_history"] = ArmCase(
            "own_history", case, _evaluate(host), explore_tokens, explore_calls, None,
            "no change suggested by own history", host.policy.label(), strategy_parsed=False,
        )

    out["raw_trajectory"] = _from_packet("raw_trajectory", case, raw_trajectory_packet(case), model_factory)
    out["distilled_packet"] = _from_packet("distilled_packet", case, distilled_packet(case.seed), model_factory)
    for name, packet in control_packets(case.seed).items():
        out[name] = _from_packet(name, case, packet, model_factory)
    return out


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def bootstrap_ci(diffs: Sequence[float], seed_parts: Tuple[Any, ...]) -> Tuple[float, float]:
    if not diffs:
        return 0.0, 0.0
    rng = random.Random(stable_int("bootstrap", *seed_parts))
    n = len(diffs)
    stats = sorted(mean([diffs[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOTSTRAP_B))
    lo = stats[int(0.025 * BOOTSTRAP_B)]
    hi = stats[int(0.975 * BOOTSTRAP_B) - 1]
    return lo, hi


def r4(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x + 0.0, 4)


def summarize_arm(seed: int, arm: str, rows: List[ArmCase], base: List[ArmCase]) -> Dict[str, Any]:
    diffs = [a.sr - b.sr for a, b in zip(rows, base)]
    lo, hi = bootstrap_ci(diffs, (seed, arm))
    candidates = [r for r in rows if r.status is not None]
    absorbed = [r for r in rows if r.status == "absorbed"]
    rollbacks = [r for r in rows if r.rollback_triggered]
    iso = [r.isolation_dsr for r in rows if r.isolation_dsr is not None]
    return {
        "arm": arm,
        "n": len(rows),
        "sr": r4(mean([r.sr for r in rows])),
        "delta_sr": r4(mean(diffs)),
        "delta_sr_ci95": [r4(lo), r4(hi)],
        "extra_tokens": r4(mean([a.tokens - b.tokens for a, b in zip(rows, base)])),
        "extra_tool_calls": r4(mean([a.tool_calls - b.tool_calls for a, b in zip(rows, base)])),
        "candidates": len(candidates),
        "accepted": len(absorbed),
        "acceptance_rate": r4(len(absorbed) / len(rows)) if arm != "no_sharing" else None,
        "rollbacks": len(rollbacks),
        "rollback_success_rate": r4(sum(1 for r in rollbacks if r.rollback_ok) / len(rollbacks)) if rollbacks else None,
        "immediate_sr": r4(mean([r.window_sr("immediate") for r in rows])),
        "delayed_sr": r4(mean([r.window_sr("delayed") for r in rows])),
        "delayed_minus_immediate": r4(mean([r.window_sr("delayed") - r.window_sr("immediate") for r in rows])),
        "isolation_dsr_mean": r4(mean(iso)) if iso else None,
        "strategy_parsed": sum(1 for r in rows if r.strategy_parsed),
        "policy_unchanged": sum(1 for r, b in zip(rows, base) if r.policy == b.policy),
        "model_calls": sum(r.model_calls for r in rows),
        "content_policy_rejections": sum(1 for r in rows if r.reason.startswith("content_policy:")),
    }


def judge_seed(arms: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Fixed rule (IMPLEMENTATION.md "Value Judgment"). Do not edit to fit a result."""
    d, o = arms["distilled_packet"], arms["own_history"]
    own_extra = o["extra_tokens"]
    cost_ok = d["extra_tokens"] <= EXTRA_COST_CAP * own_extra if own_extra > 0 else d["extra_tokens"] <= 0
    checks = {
        "distilled_delta_sr_ge_threshold": d["delta_sr"] >= DSR_THRESHOLD,
        "distilled_not_below_own_history": d["delta_sr"] >= o["delta_sr"],
        "extra_tokens_within_cap": cost_ok,
        "rollback_recovers": d["rollback_success_rate"] in (None, 1.0),
    }
    return {
        "checks": checks,
        "net_value": all(checks.values()),
        "distilled_minus_own_history": r4(d["delta_sr"] - o["delta_sr"]),
    }


def judge_controls(arms: Dict[str, Dict[str, Any]], per_case: List[Dict[str, ArmCase]]) -> Dict[str, bool]:
    wrong = [c["wrong_packet"] for c in per_case]
    return {
        "wrong_packet_isolation_dsr_le_0_all_cases": all(r.isolation_dsr is not None and r.isolation_dsr <= 0 for r in wrong),
        "wrong_packet_rolled_back_all_cases": all(r.rollback_triggered and r.status != "absorbed" for r in wrong),
        "wrong_packet_final_delta_sr_le_0": arms["wrong_packet"]["delta_sr"] <= 0,
        "empty_packet_policy_unchanged": arms["empty_packet"]["policy_unchanged"] == arms["empty_packet"]["n"]
        and arms["empty_packet"]["strategy_parsed"] == 0,
        "irrelevant_packet_policy_unchanged": arms["irrelevant_packet"]["policy_unchanged"] == arms["irrelevant_packet"]["n"]
        and arms["irrelevant_packet"]["strategy_parsed"] == 0,
        "injection_packet_rejected_by_content_policy": arms["injection_packet"]["content_policy_rejections"] == arms["injection_packet"]["n"]
        and arms["injection_packet"]["model_calls"] == 0,
    }


def judge_overall(per_seed: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    diffs = [per_seed[s]["judgement"]["distilled_minus_own_history"] for s in per_seed]
    signs = {(d > 0) - (d < 0) for d in diffs}
    consistent = len(signs) == 1 and 0 not in signs
    direction = "n/a"
    if consistent:
        direction = "distilled_packet > own_history" if signs == {1} else "distilled_packet < own_history"
    return {
        "direction_consistent": consistent,
        "direction": direction,
        "all_seeds_net_value": all(per_seed[s]["judgement"]["net_value"] for s in per_seed),
        "all_controls_pass": all(all(per_seed[s]["controls"].values()) for s in per_seed),
        "net_value": consistent and signs == {1} and all(per_seed[s]["judgement"]["net_value"] for s in per_seed),
    }


# ---------------------------------------------------------------------------
# Data products
# ---------------------------------------------------------------------------


def trajectory_packet(row: ArmCase, n: int, model_name: str) -> Dict[str, Any]:
    seed, case_id = row.case.seed, row.case.case_id
    content: Dict[str, Any] = {
        "arm": row.arm,
        "case_id": case_id,
        "scenario": "tool_call_timeout on web_search",
        "simulated_tool": True,
        "final_policy": row.policy,
        "absorption_status": row.status or "none",
        "episodes": [e.compact() for e in row.eval_episodes],
        "overhead_tokens": row.overhead_tokens,
        "overhead_tool_calls": row.overhead_tool_calls,
    }
    if row.reason:
        content["absorption_reason"] = row.reason
    if row.isolation_dsr is not None:
        content["isolation_gain"] = row.isolation_dsr
    if row.rollback_triggered:
        content["rollback_ok"] = bool(row.rollback_ok)
    return {
        "id": f"pkt-tr-s{seed}-{row.arm}-{case_id}",
        "version": "0.4",
        "type": "tool_trajectory",
        "content": content,
        "absorption": {"method": "retrieval", "target": "timeout_handling"},
        "provenance": {"source_ref": f"local_run:tr-{SCENARIO_VERSION}-s{seed}"},
        "verification_claim": {
            "expected_effect": "recovery success rate of this arm's final policy on one simulated web_search timeout case",
            "replay_hint": f"python -m experiments.timeout_recovery.run --seeds {seed} --n {n}",
            "scenario_ref": f"timeout_recovery/{SCENARIO_VERSION}/seed-{seed}/{case_id}",
            "metric": "recovery_sr",
            "value": r4(row.sr),
            "repro": {"seed": seed, "model": model_name, "n": n},
        },
    }


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run_seed(seed: int, n: int, model_factory: Callable[[], Any]) -> Dict[str, Any]:
    per_case = [run_case(Case(seed, i), model_factory) for i in range(1, n + 1)]
    base = [c["no_sharing"] for c in per_case]
    arms = {name: summarize_arm(seed, name, [c[name] for c in per_case], base) for name in ARMS + CONTROLS}
    return {
        "seed": seed,
        "n": n,
        "arms": arms,
        "judgement": judge_seed(arms),
        "controls": judge_controls(arms, per_case),
        "per_case": per_case,
    }


def run_all(seeds: Sequence[int], n: int, model_factory: Callable[[], Any]) -> Dict[str, Any]:
    per_seed = {s: run_seed(s, n, model_factory) for s in seeds}
    return {"per_seed": per_seed, "overall": judge_overall(per_seed)}


def config_dict(n: int, model_name: str) -> Dict[str, Any]:
    return {
        "scenario": SCENARIO_VERSION,
        "n_cases": n,
        "model": model_name,
        "total_token_budget": TOTAL_TOKEN_BUDGET,
        "absorb_token_budget": ABSORB_TOKEN_BUDGET,
        "dsr_threshold": DSR_THRESHOLD,
        "extra_cost_cap_vs_own_history": EXTRA_COST_CAP,
        "isolation_n": ISOLATION_N,
        "episodes_per_window": dict(WINDOW_EPISODES),
        "peer_policy": PEER_POLICY.label(),
        "ci_method": CI_METHOD,
    }


def seed_files(seed_result: Dict[str, Any], model_name: str) -> Dict[str, str]:
    seed, n = seed_result["seed"], seed_result["n"]
    traj = [dumps(trajectory_packet(c[a], n, model_name)) for a in ARMS for c in seed_result["per_case"]]
    ctrl = [dumps(trajectory_packet(c[a], n, model_name)) for a in CONTROLS for c in seed_result["per_case"]]
    summary = {
        "seed": seed,
        "config": config_dict(n, model_name),
        "arms": seed_result["arms"],
        "judgement": seed_result["judgement"],
        "controls": seed_result["controls"],
    }
    return {
        "trajectories.jsonl": "\n".join(traj) + "\n",
        "controls.jsonl": "\n".join(ctrl) + "\n",
        "summary.json": json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    }


# ---------------------------------------------------------------------------
# RESULTS.md
# ---------------------------------------------------------------------------


def _fmt(x: Optional[float], signed: bool = False, digits: int = 3) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{digits}f}" if signed else f"{x:.{digits}f}"


def _ratio(num: float, den: float) -> str:
    return "n/a" if den <= 0 else f"{num / den:.2f}"


def arm_table(arms: Dict[str, Dict[str, Any]], names: Sequence[str]) -> List[str]:
    lines = [
        "| arm | SR | ΔSR | 95% CI | extra tokens | extra tool calls | acceptance | rollback success | immediate SR | delayed SR | delayed − immediate |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in names:
        a = arms[name]
        rb = "n/a (0 rollbacks)" if a["rollback_success_rate"] is None else f"{a['rollback_success_rate']:.3f} ({a['rollbacks']})"
        lines.append(
            f"| {name} | {_fmt(a['sr'])} | {_fmt(a['delta_sr'], True)} | "
            f"[{_fmt(a['delta_sr_ci95'][0], True)}, {_fmt(a['delta_sr_ci95'][1], True)}] | "
            f"{_fmt(a['extra_tokens'], digits=1)} | {_fmt(a['extra_tool_calls'], digits=2)} | "
            f"{'n/a' if a['acceptance_rate'] is None else _fmt(a['acceptance_rate'])} | {rb} | "
            f"{_fmt(a['immediate_sr'])} | {_fmt(a['delayed_sr'])} | {_fmt(a['delayed_minus_immediate'], True)} |"
        )
    return lines


def control_table(arms: Dict[str, Dict[str, Any]]) -> List[str]:
    lines = [
        "| control | final ΔSR | isolation ΔSR (before gate) | accepted | rollbacks (success) | strategy parsed | policy unchanged | content_policy rejects | model calls |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for name in CONTROLS:
        a = arms[name]
        rb = f"{a['rollbacks']} ({'n/a' if a['rollback_success_rate'] is None else _fmt(a['rollback_success_rate'])})"
        lines.append(
            f"| {name} | {_fmt(a['delta_sr'], True)} | {_fmt(a['isolation_dsr_mean'], True)} | "
            f"{a['accepted']}/{a['n']} | {rb} | {a['strategy_parsed']}/{a['n']} | "
            f"{a['policy_unchanged']}/{a['n']} | {a['content_policy_rejections']}/{a['n']} | {a['model_calls']} |"
        )
    return lines


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def results_markdown(result: Dict[str, Any], seeds: Sequence[int], n: int, model_name: str, commit: str) -> str:
    per_seed, overall = result["per_seed"], result["overall"]
    cmd = f"python -m experiments.timeout_recovery.run --seeds {' '.join(map(str, seeds))} --n {n}"
    if model_name != MODEL_NAME:
        cmd += " --model llm"
    out: List[str] = [
        "# Timeout-recovery four-arm experiment: results",
        "",
        "Generated by `experiments/timeout_recovery/run.py`. Do not edit by hand; rerun the command.",
        "Non-normative. The tool is simulated (see `host.py`); the numbers are about this simulator and host, not about any real service.",
        "",
        "## Run",
        "",
        f"- Command: `{cmd}`",
        f"- Generated at commit: `{commit}` (the commit checked out when the file was written; the PR that adds this file generated it on top of that commit with its own changes in the work tree)",
        f"- Environment: Python {platform.python_version()} ({platform.python_implementation()}), {platform.system()} {platform.machine()}; no third-party packages used by the run",
        f"- Strategy model: `{model_name}`",
        f"- Budgets: total {TOTAL_TOKEN_BUDGET} tokens per case and arm (absorption + isolation), absorption {ABSORB_TOKEN_BUDGET}, ΔSR threshold {DSR_THRESHOLD}, extra-token cap {EXTRA_COST_CAP}× own_history, isolation N={ISOLATION_N}",
        f"- Cases: N={n} per arm per seed; each case is evaluated on {WINDOW_EPISODES['immediate']} immediate + {WINDOW_EPISODES['delayed']} delayed episodes (paired draws across arms)",
        f"- ΔSR is per case against `no_sharing` on the same draws; 95% CI: {CI_METHOD}",
        "- Extra tokens / tool calls: mean per case over `no_sharing`, including exploration, absorption and both isolation runs (before and after the change)",
        "",
    ]
    for s in seeds:
        r = per_seed[s]
        out += [f"## Seed {s}", ""]
        out += arm_table(r["arms"], ARMS)
        j = r["judgement"]
        out += ["", "Decision checks (fixed in `run.py`):", ""]
        out += [f"- {k}: {'pass' if v else 'FAIL'}" for k, v in j["checks"].items()]
        out += [f"- net value on this seed: {'yes' if j['net_value'] else 'no'}", ""]
        out += ["Controls:", ""]
        out += control_table(r["arms"])
        out += [""]
        out += [f"- {k}: {'pass' if v else 'FAIL'}" for k, v in r["controls"].items()]
        out += [""]
    out += [
        "## distilled_packet vs own_history",
        "",
        "| seed | distilled ΔSR | own_history ΔSR | difference | distilled extra tokens | own_history extra tokens | token ratio (cap 1.5) |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in seeds:
        a = per_seed[s]["arms"]
        out.append(
            f"| {s} | {_fmt(a['distilled_packet']['delta_sr'], True)} | {_fmt(a['own_history']['delta_sr'], True)} | "
            f"{_fmt(per_seed[s]['judgement']['distilled_minus_own_history'], True)} | "
            f"{_fmt(a['distilled_packet']['extra_tokens'], digits=1)} | {_fmt(a['own_history']['extra_tokens'], digits=1)} | "
            f"{_ratio(a['distilled_packet']['extra_tokens'], a['own_history']['extra_tokens'])} |"
        )
    out += [
        "",
        f"- Direction consistent across seeds: {'yes' if overall['direction_consistent'] else 'no'} ({overall['direction']})",
        f"- Net value on every seed: {'yes' if overall['all_seeds_net_value'] else 'no'}",
        f"- All controls pass: {'yes' if overall['all_controls_pass'] else 'no'}",
        "",
        "## Conclusion",
        "",
        f"Overall verdict (fixed rule): **{'net value' if overall['net_value'] else 'no net value'}**.",
        "",
    ]
    failed = sorted({k for s in seeds for k, v in per_seed[s]["judgement"]["checks"].items() if not v})
    if failed:
        out.append("Checks that failed on at least one seed: " + ", ".join(f"`{k}`" for k in failed) + ".")
        out.append("")
    out += [
        "How to read it: ΔSR, acceptance and rollback are judged on the success rate; the extra-token check counts "
        "the absorbed block as context on every later episode (prompt_injection keeps it in the prompt), plus the "
        "absorption and both isolation runs. A failed check is reported as a failure; thresholds are not adjusted.",
        "",
        "Limits: one simulated tool, and the peer policy was written for this simulator, so a large distilled gain is "
        "partly by construction; the default strategy model is a rule parser, not an LLM; own_history uses a fixed "
        "local rule with no tuned backoff base. Reproducibility of the whole run (byte-identical output for the same "
        "seed) is checked by `tests/experiments/test_timeout_recovery.py`.",
        "",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_model_factory(kind: str) -> Tuple[Callable[[], Any], str]:
    if kind == "rule":
        return RuleStrategyModel, MODEL_NAME
    model = LLMStrategyModel()
    if not model.available():
        raise SystemExit("--model llm needs A2A_TR_LLM_URL, A2A_TR_LLM_MODEL and A2A_TR_LLM_KEY; skipped")
    return (lambda: model), model.name


def _write(path: Path, text: str, written: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    written[str(path)] = hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_outputs(result: Dict[str, Any], seeds: Sequence[int], n: int, model_name: str,
                  data_dir: Optional[Path], results_path: Optional[Path], commit: str,
                  full_seeds: Optional[Sequence[int]] = None,
                  summary_dir: Optional[Path] = None) -> Dict[str, str]:
    written: Dict[str, str] = {}
    for s in seeds:
        files = seed_files(result["per_seed"][s], model_name)
        if summary_dir is not None:
            _write(summary_dir / f"seed-{s}.json", files.pop("summary.json"), written)
        if data_dir is not None and (full_seeds is None or s in full_seeds):
            for name in ("trajectories.jsonl", "controls.jsonl"):
                _write(data_dir / str(s) / name, files[name], written)
    if results_path is not None:
        _write(results_path, results_markdown(result, seeds, n, model_name, commit), written)
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 7, 2026])
    ap.add_argument("--n", type=int, default=30, help="cases per arm per seed (>= 30 for the reported run)")
    ap.add_argument("--model", choices=("rule", "llm"), default="rule")
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--summary-dir", type=Path, default=DEFAULT_SUMMARY_DIR)
    ap.add_argument("--full-seeds", type=int, nargs="*", default=None,
                    help="write trajectories only for these seeds (summaries are always written); default all")
    ap.add_argument("--commit", default=None, help="commit id to print in RESULTS.md (default: git HEAD)")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

    factory, model_name = build_model_factory(args.model)
    result = run_all(args.seeds, args.n, factory)
    commit = args.commit or git_commit()
    if not args.no_write:
        write_outputs(result, args.seeds, args.n, model_name, args.data_dir, args.results, commit,
                      args.full_seeds, args.summary_dir)
    for s in args.seeds:
        print(f"seed {s}")
        print("\n".join(arm_table(result["per_seed"][s]["arms"], ARMS)))
        print("\n".join(control_table(result["per_seed"][s]["arms"])))
        print(dumps(result["per_seed"][s]["judgement"]))
        print(dumps(result["per_seed"][s]["controls"]))
    print(dumps(result["overall"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
