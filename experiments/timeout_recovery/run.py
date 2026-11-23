"""Four-arm timeout-recovery experiment runner (non-normative).

    python -m experiments.timeout_recovery.run --seeds 42 7 2026 --n 30

By default nothing is written: the tables and judgements go to stdout, so a
trial run cannot overwrite committed files. Files are written only when asked:

- ``--results [PATH]`` writes the Markdown report (default PATH:
  ``experiments/timeout_recovery/RESULTS.md``);
- ``--summary-dir [DIR]`` writes per-seed aggregates ``seed-<seed>.json``
  (default DIR: ``experiments/timeout_recovery/summaries``, kept out of
  ``datasets/``, whose ``*.json`` files must be protocol messages);
- ``--data-dir [DIR]`` writes raw trajectories (v0.4 Experience Packets, one
  per line) to ``DIR/<seed>/`` (default DIR: ``datasets/timeout_recovery/runs``).

Regenerate the committed files with::

    python -m experiments.timeout_recovery.run --seeds 42 7 2026 --n 30 \
        --results --summary-dir --data-dir

Output is byte-identical for the same arguments: no wall-clock time is written,
ids and draws come from sha256.

Decision rules are fixed here (``judge_seed`` / ``judge_overall``) and are
printed as-is, including a negative verdict. Since WP1 they are applied to two
token views of the same run (A: the absorbed text is carried in context on
every episode; B: only the parsed parameters are carried); the rules and
thresholds are the same for both.
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
    OWN_SEARCH_TOKEN_BUDGET,
    SEARCH_BASE_BACKOFF_MS,
    SEARCH_DROP_STALE,
    SEARCH_MAX_ATTEMPTS,
    SEARCH_PASSES,
    approx_tokens,
    naive_policy,
    policy_from_params,
    run_window,
    search_own_policy,
    stable_int,
)
from ai_oriented_a2a import wrap_untrusted

from .packets import (
    MEDIOCRE_POLICY,
    PEER_POLICY,
    WORKING_POLICY,
    control_packets,
    distilled_packet,
    mediocre_packet,
    raw_trajectory_packet,
    working_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "datasets" / "timeout_recovery" / "runs"
DEFAULT_RESULTS = Path(__file__).resolve().parent / "RESULTS.md"
DEFAULT_SUMMARY_DIR = Path(__file__).resolve().parent / "summaries"
ARMS = ("no_sharing", "own_history", "raw_trajectory", "distilled_packet")
# WP1: a distilled packet from a peer only slightly better than naive. Reported
# next to the four arms; the fixed verdict is still about distilled_packet.
EXTRA_ARMS = ("mediocre_packet",)
CONTROLS = ("wrong_packet", "empty_packet", "irrelevant_packet", "injection_packet")
# WP1: the wrong packet on a host that already recovers (WORKING_POLICY), and
# the working host itself as its baseline. Ungated rows apply the wrong
# parameters without the gate (counterfactual, not shipped as data).
WORKING_CONTROLS = ("wrong_packet_working_host",)
WRONG_HOSTS = ("naive_host", "working_host")
TOKEN_VIEWS = {
    "A": ("extra_tokens", "absorbed text in context on every episode (as in PR #51)"),
    "B": ("extra_tokens_b", "parsed parameters only in context on every episode"),
}
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
    overhead_tokens_b: int = 0  # view B of overhead_tokens (parameters only in context)
    searched_policy: str = ""  # own_history only: the search's pick, before the gate

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
    def tokens_b(self) -> int:
        return self.overhead_tokens_b + sum(e.tokens_b for e in self.eval_episodes)

    @property
    def tool_calls(self) -> int:
        return self.overhead_tool_calls + sum(e.tool_calls for e in self.eval_episodes)


def _evaluate(host: Host) -> List[Episode]:
    return run_window(host.policy, host.case, "immediate") + run_window(host.policy, host.case, "delayed")


def _from_packet(arm: str, case: Case, packet: Dict[str, Any], model_factory: Callable[[], Any],
                 host: Optional[Host] = None) -> ArmCase:
    host = host or Host(case, model_factory())
    reply = host.handle_message(packet)
    rec = host.log[-1] if host.log else None
    metrics = (reply.get("effect_summary") or {}).get("metrics") or {}
    return ArmCase(
        arm=arm,
        case=case,
        eval_episodes=_evaluate(host),
        overhead_tokens=(rec.absorb_tokens + rec.isolation_tokens) if rec else 0,
        overhead_tokens_b=(rec.absorb_tokens + rec.isolation_tokens_b) if rec else 0,
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

    # own_history (WP1): searches the same parameter space a packet states,
    # on its own exploration runs, then goes through the same gate.
    host = Host(case, model_factory())
    search = search_own_policy(case, host.policy)
    explore_tokens = sum(e.tokens for e in search.episodes)
    explore_tokens_b = sum(e.tokens_b for e in search.episodes)
    explore_calls = sum(e.tool_calls for e in search.episodes)
    picked = policy_from_params(host.policy, search.params).label()
    if search.params:
        note_tokens = approx_tokens(search.params)  # the learned parameters kept in context
        rec = host.gate("own_history", search.params, search.evidence, 0, context_tokens=note_tokens)
        out["own_history"] = ArmCase(
            "own_history", case, _evaluate(host),
            explore_tokens + rec.isolation_tokens, explore_calls + rec.isolation_tool_calls,
            rec.status, rec.reason, host.policy.label(), rec.rollback_triggered, rec.rollback_ok,
            rec.isolation_dsr, True, overhead_tokens_b=explore_tokens_b + rec.isolation_tokens_b,
            searched_policy=picked,
        )
    else:
        out["own_history"] = ArmCase(
            "own_history", case, _evaluate(host), explore_tokens, explore_calls, None,
            "no change suggested by own history", host.policy.label(), strategy_parsed=False,
            overhead_tokens_b=explore_tokens_b, searched_policy=picked,
        )

    out["raw_trajectory"] = _from_packet("raw_trajectory", case, raw_trajectory_packet(case), model_factory)
    out["distilled_packet"] = _from_packet("distilled_packet", case, distilled_packet(case.seed), model_factory)
    out["mediocre_packet"] = _from_packet("mediocre_packet", case, mediocre_packet(case.seed), model_factory)
    controls = control_packets(case.seed)
    for name, packet in controls.items():
        out[name] = _from_packet(name, case, packet, model_factory)

    # Wrong packet on a naive and on a working host (WP1). Gated: through the
    # host as usual. Ungated: the same parsed parameters applied with no gate,
    # to measure what the advice itself does to each host.
    wrong = controls["wrong_packet"]
    host = Host(case, model_factory())
    host.policy = working_policy()
    out["working_host"] = ArmCase("working_host", case, _evaluate(host), 0, 0, None, policy=host.policy.label())
    host = Host(case, model_factory())
    host.policy = working_policy()
    out["wrong_packet_working_host"] = _from_packet("wrong_packet_working_host", case, wrong, model_factory, host)
    wrong_params, _ = model_factory().derive(wrap_untrusted(wrong))
    for name, start in (("naive_host", naive_policy()), ("working_host", working_policy())):
        ungated = Host(case, model_factory())
        ungated.policy = policy_from_params(start, wrong_params)
        out[f"wrong_ungated_{name}"] = ArmCase(
            f"wrong_ungated_{name}", case, _evaluate(ungated), 0, 0, None, policy=ungated.policy.label(),
        )
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
        "extra_tokens_b": r4(mean([a.tokens_b - b.tokens_b for a, b in zip(rows, base)])),
        # Split of extra tokens (information only; extra = one_time + eval_extra,
        # no_sharing has no one-time cost): one-time = exploration/search,
        # absorption and both isolation runs; eval_extra = evaluation episodes
        # over no_sharing (resident context + tool-call differences).
        "one_time_tokens": r4(mean([a.overhead_tokens - b.overhead_tokens for a, b in zip(rows, base)])),
        "one_time_tokens_b": r4(mean([a.overhead_tokens_b - b.overhead_tokens_b for a, b in zip(rows, base)])),
        "eval_extra_tokens": r4(mean([
            sum(e.tokens for e in a.eval_episodes) - sum(e.tokens for e in b.eval_episodes) for a, b in zip(rows, base)])),
        "eval_extra_tokens_b": r4(mean([
            sum(e.tokens_b for e in a.eval_episodes) - sum(e.tokens_b for e in b.eval_episodes) for a, b in zip(rows, base)])),
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


def token_view(arms: Dict[str, Dict[str, Any]], view: str, distilled: str = "distilled_packet") -> Dict[str, Dict[str, Any]]:
    """Arms as ``judge_seed`` sees them under a token view.

    ``extra_tokens`` is replaced by the view's column; ``distilled`` names the
    arm put in the ``distilled_packet`` slot (the mediocre arm is judged by the
    same rule this way, for information only). The rule itself is unchanged.
    """
    key = TOKEN_VIEWS[view][0]
    out = {name: dict(a, extra_tokens=a[key]) for name, a in arms.items()}
    out["distilled_packet"] = out[distilled]
    return out


def judge_views(arms: Dict[str, Dict[str, Any]], distilled: str = "distilled_packet") -> Dict[str, Dict[str, Any]]:
    return {view: judge_seed(token_view(arms, view, distilled)) for view in TOKEN_VIEWS}


AMORTIZE_ARMS = ("own_history", "raw_trajectory", "distilled_packet", "mediocre_packet")


def amortization(arms: Dict[str, Dict[str, Any]], n: int) -> Dict[str, Any]:
    """Information only, never used by ``judge_seed`` / ``judge_overall``.

    Assumes the one-time cost (own_history's search + isolation; a packet's
    absorption + isolation) is paid once and reused by all ``n`` cases, so a
    case carries ``one_time / n`` plus its own evaluation-episode tokens. Ratios
    are against own_history amortized the same way, per token view.
    """
    out: Dict[str, Any] = {}
    for view, (_, _desc) in TOKEN_VIEWS.items():
        suffix = "" if view == "A" else "_b"
        rows = {}
        for name in AMORTIZE_ARMS:
            a = arms[name]
            one, ev = a[f"one_time_tokens{suffix}"], a[f"eval_extra_tokens{suffix}"]
            rows[name] = {"one_time": one, "eval_extra": ev, "amortized_extra": r4(one / n + ev)}
        own = rows["own_history"]["amortized_extra"]
        for name, row in rows.items():
            row["ratio_vs_own_history"] = r4(row["amortized_extra"] / own) if own > 0 else None
            row["within_cap"] = row["ratio_vs_own_history"] is not None and row["ratio_vs_own_history"] <= EXTRA_COST_CAP
        out[view] = rows
    return out


def own_history_picks(per_case: List[Dict[str, ArmCase]]) -> Dict[str, Any]:
    """Information only: what the own_history search picked, and why it missed the peer.

    ``peer_vs_pick_on_explore`` replays PEER_POLICY on the same explore draws
    (a counterfactual, not counted in any token column) and compares its
    successes with the pick's: "tie" means the 3 explore episodes could not
    tell them apart, "pick_better" means the pick scored more on those draws,
    "peer_better" means the coordinate search missed a policy that scored more.
    """
    picked: Dict[str, int] = {}
    final: Dict[str, int] = {}
    cmp = {"picked_peer": 0, "tie": 0, "pick_better": 0, "peer_better": 0}
    peer_label = PEER_POLICY.label()
    for c in per_case:
        row = c["own_history"]
        picked[row.searched_policy] = picked.get(row.searched_policy, 0) + 1
        final[row.policy] = final.get(row.policy, 0) + 1
        if row.searched_policy == peer_label:
            cmp["picked_peer"] += 1
            continue
        case = row.case
        peer = sum(1 for e in run_window(PEER_POLICY, case, "explore") if e.success)
        pick = sum(1 for e in run_window(policy_from_params(naive_policy(), _params_from_label(row.searched_policy)),
                                         case, "explore") if e.success)
        cmp["tie" if peer == pick else ("pick_better" if pick > peer else "peer_better")] += 1
    order = lambda d: dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))  # noqa: E731
    return {
        "picked": order(picked),
        "final_after_gate": order(final),
        "picked_peer_policy": picked.get(peer_label, 0),
        "final_peer_policy": final.get(peer_label, 0),
        "peer_vs_pick_on_explore": cmp,
    }


def _params_from_label(label: str) -> Dict[str, Any]:
    """Inverse of ``Policy.label()`` for the policies the search can pick."""
    parts = dict(item.split("=", 1) for item in label.split(","))
    backoff = parts["backoff"]
    params: Dict[str, Any] = {"backoff": backoff != "none", "drop_stale_partials": parts["drop_stale"] == "True"}
    if backoff != "none":
        params["base_backoff_ms"] = int(backoff[len("exp"):-len("ms")])
    params["max_attempts"] = None if parts["max_attempts"] == "unbounded" else int(parts["max_attempts"])
    return params


def summarize_wrong_host(seed: int, host: str, gated: List[ArmCase], ungated: List[ArmCase],
                         base: List[ArmCase]) -> Dict[str, Any]:
    """Wrong packet on one host type. ΔSR is against that host without the packet."""
    g = [a.sr - b.sr for a, b in zip(gated, base)]
    u = [a.sr - b.sr for a, b in zip(ungated, base)]
    glo, ghi = bootstrap_ci(g, (seed, "wrong", host, "gated"))
    ulo, uhi = bootstrap_ci(u, (seed, "wrong", host, "ungated"))
    rollbacks = [r for r in gated if r.rollback_triggered]
    iso = [r.isolation_dsr for r in gated if r.isolation_dsr is not None]
    if uhi < 0:
        effect = "harmful"
    elif ulo > 0:
        effect = "helpful"
    else:
        effect = "no measurable effect"
    return {
        "host": host,
        "host_policy": base[0].policy if base else "",
        "n": len(gated),
        "host_sr": r4(mean([b.sr for b in base])),
        "ungated_sr": r4(mean([a.sr for a in ungated])),
        "ungated_delta_sr": r4(mean(u)),
        "ungated_delta_sr_ci95": [r4(ulo), r4(uhi)],
        "ungated_effect": effect,
        "isolation_dsr_mean": r4(mean(iso)) if iso else None,
        "isolation_dsr_negative_cases": sum(1 for x in iso if x < 0),
        "gated_delta_sr": r4(mean(g)),
        "gated_delta_sr_ci95": [r4(glo), r4(ghi)],
        "accepted": sum(1 for r in gated if r.status == "absorbed"),
        "rollbacks": len(rollbacks),
        "rollback_success_rate": r4(sum(1 for r in rollbacks if r.rollback_ok) / len(rollbacks)) if rollbacks else None,
        "policy_unchanged": sum(1 for a, b in zip(gated, base) if a.policy == b.policy),
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


def judge_overall(per_seed: Dict[int, Dict[str, Any]], key: str = "judgement") -> Dict[str, Any]:
    """``key`` picks which per-seed judgement to combine (one per token view)."""
    diffs = [per_seed[s][key]["distilled_minus_own_history"] for s in per_seed]
    signs = {(d > 0) - (d < 0) for d in diffs}
    consistent = len(signs) == 1 and 0 not in signs
    direction = "n/a"
    if consistent:
        direction = "distilled_packet > own_history" if signs == {1} else "distilled_packet < own_history"
    return {
        "direction_consistent": consistent,
        "direction": direction,
        "all_seeds_net_value": all(per_seed[s][key]["net_value"] for s in per_seed),
        "all_controls_pass": all(all(per_seed[s]["controls"].values()) for s in per_seed),
        "net_value": consistent and signs == {1} and all(per_seed[s][key]["net_value"] for s in per_seed),
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
        "overhead_tokens_params_only": row.overhead_tokens_b,
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
    arms = {name: summarize_arm(seed, name, [c[name] for c in per_case], base) for name in ARMS + EXTRA_ARMS + CONTROLS}
    working = [c["working_host"] for c in per_case]
    wrong_by_host = {
        "naive_host": summarize_wrong_host(
            seed, "naive_host", [c["wrong_packet"] for c in per_case], [c["wrong_ungated_naive_host"] for c in per_case], base),
        "working_host": summarize_wrong_host(
            seed, "working_host", [c["wrong_packet_working_host"] for c in per_case],
            [c["wrong_ungated_working_host"] for c in per_case], working),
    }
    views = judge_views(arms)
    return {
        "seed": seed,
        "n": n,
        "arms": arms,
        "judgement": views["A"],
        "judgement_params_only": views["B"],
        "mediocre_judgement": judge_views(arms, "mediocre_packet"),
        "controls": judge_controls(arms, per_case),
        "wrong_by_host": wrong_by_host,
        "amortization": amortization(arms, n),
        "own_history_picks": own_history_picks(per_case),
        "per_case": per_case,
    }


def run_all(seeds: Sequence[int], n: int, model_factory: Callable[[], Any]) -> Dict[str, Any]:
    per_seed = {s: run_seed(s, n, model_factory) for s in seeds}
    return {
        "per_seed": per_seed,
        "overall": judge_overall(per_seed),
        "overall_params_only": judge_overall(per_seed, "judgement_params_only"),
    }


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
        "mediocre_peer_policy": MEDIOCRE_POLICY.label(),
        "working_host_policy": WORKING_POLICY.label(),
        "own_history_search": {
            "space": {
                "drop_stale_partials": list(SEARCH_DROP_STALE),
                "backoff": [False] + [f"exp{ms}ms" for ms in SEARCH_BASE_BACKOFF_MS],
                "max_attempts": list(SEARCH_MAX_ATTEMPTS),
            },
            "order": ["drop_stale_partials", "backoff", "max_attempts"],
            "passes": SEARCH_PASSES,
            "episodes_per_candidate": WINDOW_EPISODES["explore"],
            "token_budget": OWN_SEARCH_TOKEN_BUDGET,
        },
        "token_views": {view: desc for view, (_, desc) in TOKEN_VIEWS.items()},
        "ci_method": CI_METHOD,
    }


def seed_files(seed_result: Dict[str, Any], model_name: str) -> Dict[str, str]:
    seed, n = seed_result["seed"], seed_result["n"]
    traj = [dumps(trajectory_packet(c[a], n, model_name)) for a in ARMS + EXTRA_ARMS for c in seed_result["per_case"]]
    ctrl = [dumps(trajectory_packet(c[a], n, model_name)) for a in CONTROLS + WORKING_CONTROLS for c in seed_result["per_case"]]
    summary = {
        "seed": seed,
        "config": config_dict(n, model_name),
        "arms": seed_result["arms"],
        "judgement": seed_result["judgement"],
        "judgement_params_only": seed_result["judgement_params_only"],
        "mediocre_judgement": seed_result["mediocre_judgement"],
        "controls": seed_result["controls"],
        "wrong_by_host": seed_result["wrong_by_host"],
        "amortization": seed_result["amortization"],
        "own_history_picks": seed_result["own_history_picks"],
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
        "| arm | SR | ΔSR | 95% CI | extra tokens A | extra tokens B | extra tool calls | acceptance | rollback success | immediate SR | delayed SR | delayed − immediate |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in names:
        a = arms[name]
        rb = "n/a (0 rollbacks)" if a["rollback_success_rate"] is None else f"{a['rollback_success_rate']:.3f} ({a['rollbacks']})"
        lines.append(
            f"| {name} | {_fmt(a['sr'])} | {_fmt(a['delta_sr'], True)} | "
            f"[{_fmt(a['delta_sr_ci95'][0], True)}, {_fmt(a['delta_sr_ci95'][1], True)}] | "
            f"{_fmt(a['extra_tokens'], digits=1)} | {_fmt(a['extra_tokens_b'], digits=1)} | {_fmt(a['extra_tool_calls'], digits=2)} | "
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


def wrong_host_table(wrong: Dict[str, Dict[str, Any]]) -> List[str]:
    lines = [
        "| host | host policy | host SR | ungated ΔSR (no gate) | 95% CI | effect of the advice | isolation ΔSR (gate view) | negative isolation cases | gated final ΔSR | accepted | rollbacks (success) |",
        "| --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for name in WRONG_HOSTS:
        w = wrong[name]
        rb = f"{w['rollbacks']} ({'n/a' if w['rollback_success_rate'] is None else _fmt(w['rollback_success_rate'])})"
        lines.append(
            f"| {name} | `{w['host_policy']}` | {_fmt(w['host_sr'])} | {_fmt(w['ungated_delta_sr'], True)} | "
            f"[{_fmt(w['ungated_delta_sr_ci95'][0], True)}, {_fmt(w['ungated_delta_sr_ci95'][1], True)}] | "
            f"{w['ungated_effect']} | {_fmt(w['isolation_dsr_mean'], True)} | {w['isolation_dsr_negative_cases']}/{w['n']} | "
            f"{_fmt(w['gated_delta_sr'], True)} | {w['accepted']}/{w['n']} | {rb} |"
        )
    return lines


def _checks(j: Dict[str, Any]) -> List[str]:
    out = [f"- {k}: {'pass' if v else 'FAIL'}" for k, v in j["checks"].items()]
    return out + [f"- net value on this seed: {'yes' if j['net_value'] else 'no'}"]


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _vs_own_table(per_seed: Dict[int, Dict[str, Any]], seeds: Sequence[int], arm: str, judgements: str) -> List[str]:
    out = [
        f"| seed | {arm} ΔSR | own_history ΔSR | difference | {arm} extra tokens A | own_history extra tokens A | ratio A (cap {EXTRA_COST_CAP}) | {arm} extra tokens B | own_history extra tokens B | ratio B (cap {EXTRA_COST_CAP}) | net value A | net value B |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for s in seeds:
        a = per_seed[s]["arms"]
        d, o = a[arm], a["own_history"]
        ja, jb = (per_seed[s]["judgement"], per_seed[s]["judgement_params_only"]) if judgements == "verdict" else (
            per_seed[s]["mediocre_judgement"]["A"], per_seed[s]["mediocre_judgement"]["B"])
        out.append(
            f"| {s} | {_fmt(d['delta_sr'], True)} | {_fmt(o['delta_sr'], True)} | {_fmt(ja['distilled_minus_own_history'], True)} | "
            f"{_fmt(d['extra_tokens'], digits=1)} | {_fmt(o['extra_tokens'], digits=1)} | {_ratio(d['extra_tokens'], o['extra_tokens'])} | "
            f"{_fmt(d['extra_tokens_b'], digits=1)} | {_fmt(o['extra_tokens_b'], digits=1)} | {_ratio(d['extra_tokens_b'], o['extra_tokens_b'])} | "
            f"{'yes' if ja['net_value'] else 'no'} | {'yes' if jb['net_value'] else 'no'} |"
        )
    return out


def amortization_section(per_seed: Dict[int, Dict[str, Any]], seeds: Sequence[int], n: int) -> List[str]:
    out = [
        "## Amortization sensitivity (information only, not part of the verdict)",
        "",
        "Extra tokens per case = one-time cost + evaluation-episode extra. One-time: own_history's search and isolation; "
        "a packet's absorption and isolation. Evaluation extra: the evaluation episodes over no_sharing (resident "
        f"context plus tool-call differences). Amortized = one-time / N (N={n}) + evaluation extra, i.e. one search or "
        "one absorption reused by every case. Ratios are against own_history amortized the same way; "
        f"cap {EXTRA_COST_CAP}. The fixed rule above uses the unamortized numbers.",
        "",
        "| seed | arm | one-time A | eval extra A | amortized A | ratio A | one-time B | eval extra B | amortized B | ratio B |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in seeds:
        am = per_seed[s]["amortization"]
        for name in AMORTIZE_ARMS:
            a, b = am["A"][name], am["B"][name]
            out.append(
                f"| {s} | {name} | {_fmt(a['one_time'], digits=1)} | {_fmt(a['eval_extra'], digits=1)} | "
                f"{_fmt(a['amortized_extra'], digits=1)} | {_fmt(a['ratio_vs_own_history'], digits=2)} | "
                f"{_fmt(b['one_time'], digits=1)} | {_fmt(b['eval_extra'], digits=1)} | "
                f"{_fmt(b['amortized_extra'], digits=1)} | {_fmt(b['ratio_vs_own_history'], digits=2)} |"
            )
    return out + [""]


def own_history_picks_section(per_seed: Dict[int, Dict[str, Any]], seeds: Sequence[int]) -> List[str]:
    out = [
        "## own_history search picks (information only)",
        "",
        f"Policy the search picked per case (before the gate), and how often it is the peer's policy `{PEER_POLICY.label()}`. "
        "\"Peer vs pick on explore\" replays the peer policy on the same 3 explore episodes (counterfactual, not counted in "
        "any token column): tie = the episodes cannot tell them apart; pick better = the pick scored more on those draws; "
        "peer better = the coordinate search missed a policy that scored more.",
        "",
        "| seed | picked policy | cases (picked) | cases (kept after gate) |",
        "| ---: | --- | ---: | ---: |",
    ]
    for s in seeds:
        p = per_seed[s]["own_history_picks"]
        for label in sorted(set(p["picked"]) | set(p["final_after_gate"]), key=lambda k: (-p["picked"].get(k, 0), k)):
            out.append(f"| {s} | `{label}` | {p['picked'].get(label, 0)} | {p['final_after_gate'].get(label, 0)} |")
    out += [
        "",
        "| seed | picked the peer policy | kept the peer policy | peer vs pick on explore: tie | pick better | peer better |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in seeds:
        p = per_seed[s]["own_history_picks"]
        c = p["peer_vs_pick_on_explore"]
        out.append(f"| {s} | {p['picked_peer_policy']} | {p['final_peer_policy']} | {c['tie']} | {c['pick_better']} | {c['peer_better']} |")
    return out + [""]


def results_markdown(result: Dict[str, Any], seeds: Sequence[int], n: int, model_name: str, commit: str) -> str:
    per_seed = result["per_seed"]
    overall = {"A": result["overall"], "B": result["overall_params_only"]}
    cmd = f"python -m experiments.timeout_recovery.run --seeds {' '.join(map(str, seeds))} --n {n}"
    if model_name != MODEL_NAME:
        cmd += " --model llm"
    cmd += " --results --summary-dir --data-dir"
    out: List[str] = [
        "# Timeout-recovery four-arm experiment: results",
        "",
        "Generated by `experiments/timeout_recovery/run.py`. Do not edit by hand; rerun the command.",
        "Non-normative. The tool is simulated (see `host.py`); the numbers are about this simulator and host, not about any real service.",
        "",
        "## Run",
        "",
        f"- Command: `{cmd}`",
        f"- Generated at commit: `{commit}` (the commit checked out when the file was written; the PR that changes this file generated it on top of that commit with its own changes in the work tree)",
        f"- Environment: Python {platform.python_version()} ({platform.python_implementation()}), {platform.system()} {platform.machine()}; no third-party packages used by the run",
        f"- Strategy model: `{model_name}`",
        f"- Budgets: total {TOTAL_TOKEN_BUDGET} tokens per case and arm (absorption + isolation), absorption {ABSORB_TOKEN_BUDGET}, ΔSR threshold {DSR_THRESHOLD}, extra-token cap {EXTRA_COST_CAP}× own_history, isolation N={ISOLATION_N}",
        f"- Cases: N={n} per arm per seed; each case is evaluated on {WINDOW_EPISODES['immediate']} immediate + {WINDOW_EPISODES['delayed']} delayed episodes (paired draws across arms)",
        f"- ΔSR is per case against `no_sharing` on the same draws; 95% CI: {CI_METHOD}",
        "- Extra tokens / tool calls: mean per case over `no_sharing`, including exploration or search, absorption and both isolation runs (before and after the change)",
        "- Token views (same run, two ways of counting; the decision rule is applied to each): "
        "**A** counts the absorbed block as context on every later episode (prompt_injection keeps it in the prompt; the accounting of PR #51); "
        "**B** counts only the parsed parameters as context on every episode. Both count the one-time read of the packet, the tool calls, and own_history's search. "
        "The gate's own token budget is always checked under A.",
        f"- own_history: coordinate search over the parameter space a distilled packet states "
        f"(drop_stale_partials {list(SEARCH_DROP_STALE)}, then backoff off or exponential from {list(SEARCH_BASE_BACKOFF_MS)} ms, "
        f"then max_attempts {list(SEARCH_MAX_ATTEMPTS)}), {SEARCH_PASSES} passes, {WINDOW_EPISODES['explore']} paired exploration episodes per candidate, "
        f"stopping at {OWN_SEARCH_TOKEN_BUDGET} tool-call tokens; the pick then goes through the same isolation gate as a packet. "
        f"The peer's policy (`{PEER_POLICY.label()}`) is inside this space",
        f"- mediocre_packet: a distilled packet from a peer running `{MEDIOCRE_POLICY.label()}`, only slightly better than the naive receiver; "
        "judged by the same rule for information only (the verdict is about distilled_packet)",
        f"- Wrong packet: sent to the naive receiver and to a working receiver (`{WORKING_POLICY.label()}`, configured locally). "
        "\"Ungated\" applies its parameters with no gate, to show what the advice does; \"gated\" is what the host keeps",
        "",
    ]
    for s in seeds:
        r = per_seed[s]
        out += [f"## Seed {s}", ""]
        out += arm_table(r["arms"], ARMS + EXTRA_ARMS)
        out += ["", "Decision checks (fixed in `run.py`), token view A:", ""]
        out += _checks(r["judgement"])
        out += ["", "Decision checks, token view B:", ""]
        out += _checks(r["judgement_params_only"])
        out += ["", "mediocre_packet in the distilled_packet slot (information only, not part of the verdict):", ""]
        for view in TOKEN_VIEWS:
            mj = r["mediocre_judgement"][view]
            failed = [k for k, v in mj["checks"].items() if not v]
            out.append(
                f"- view {view}: net value {'yes' if mj['net_value'] else 'no'}"
                + (f" (failed: {', '.join(f'`{k}`' for k in failed)})" if failed else "")
            )
        out += ["", "Controls:", ""]
        out += control_table(r["arms"])
        out += [""]
        out += [f"- {k}: {'pass' if v else 'FAIL'}" for k, v in r["controls"].items()]
        out += ["", "Wrong packet by host (ΔSR against the same host without the packet):", ""]
        out += wrong_host_table(r["wrong_by_host"])
        out += [""]
    out += ["## distilled_packet vs own_history", ""]
    out += _vs_own_table(per_seed, seeds, "distilled_packet", "verdict")
    out += ["", "## mediocre_packet vs own_history (information only)", ""]
    out += _vs_own_table(per_seed, seeds, "mediocre_packet", "mediocre")
    out += [""]
    out += amortization_section(per_seed, seeds, n)
    out += own_history_picks_section(per_seed, seeds)
    for view in TOKEN_VIEWS:
        o = overall[view]
        out += [
            f"- View {view}: direction consistent across seeds: {'yes' if o['direction_consistent'] else 'no'} ({o['direction']}); "
            f"net value on every seed: {'yes' if o['all_seeds_net_value'] else 'no'}; all controls pass: {'yes' if o['all_controls_pass'] else 'no'}",
        ]
    out += ["", "## Conclusion", ""]
    for view in TOKEN_VIEWS:
        o = overall[view]
        key = "judgement" if view == "A" else "judgement_params_only"
        out.append(f"Overall verdict (fixed rule), token view {view}: **{'net value' if o['net_value'] else 'no net value'}**.")
        failed = sorted({k for s in seeds for k, v in per_seed[s][key]["checks"].items() if not v})
        if failed:
            out.append(f"Checks that failed on at least one seed under view {view}: " + ", ".join(f"`{k}`" for k in failed) + ".")
        out.append("")
    amort_fail = sorted({f"{v}" for s in seeds for v in TOKEN_VIEWS
                         if not per_seed[s]["amortization"][v]["distilled_packet"]["within_cap"]})
    out += [
        "The verdict is sensitive to amortization: it holds when every case pays its own one-time cost (the fixed rule); "
        "if one-time costs are shared across the N cases, distilled_packet's extra-token ratio is "
        + ", ".join(
            f"{_fmt(min(per_seed[s]['amortization'][v]['distilled_packet']['ratio_vs_own_history'] for s in seeds), digits=2)}–"
            f"{_fmt(max(per_seed[s]['amortization'][v]['distilled_packet']['ratio_vs_own_history'] for s in seeds), digits=2)} "
            f"under view {v}" for v in TOKEN_VIEWS)
        + f" (cap {EXTRA_COST_CAP}; over the cap under: {', '.join(amort_fail) if amort_fail else 'none'}). "
        "Whether one-time costs can be reused across cases is an assumption, so both are reported; the amortized "
        "numbers are information only and do not enter the verdict.",
        "",
        "own_history's search picked the peer policy in "
        + ", ".join(f"{per_seed[s]['own_history_picks']['picked_peer_policy']}/{n}" for s in seeds)
        + " cases (seeds " + ", ".join(map(str, seeds)) + "); on its 3 explore episodes the peer policy tied the pick in "
        + ", ".join(str(per_seed[s]["own_history_picks"]["peer_vs_pick_on_explore"]["tie"]) for s in seeds)
        + " cases and scored more than the pick in "
        + ", ".join(str(per_seed[s]["own_history_picks"]["peer_vs_pick_on_explore"]["peer_better"]) for s in seeds)
        + " cases. Ties are a sample-size limit of the 3 explore episodes; \"peer better\" cases are a search miss. "
        "Information only.",
        "",
    ]
    out += [
        "How to read it: ΔSR, acceptance and rollback are judged on the success rate; the extra-token check compares "
        "distilled_packet with own_history under each token view. own_history now pays for its search (exploration "
        "tool calls), so its extra tokens are larger than in PR #51 and the ratio can fall even when the packet side "
        "is unchanged; read own_history's ΔSR and extra tool calls next to the ratio. A failed check is reported as a failure; thresholds are not adjusted.",
        "",
        "Limits: one simulated tool, and the peer policy was written for this simulator, so a large distilled gain is "
        "partly by construction; own_history searches the same space but only on its own few exploration episodes, so "
        "it can overfit them; the default strategy model is a rule parser, not an LLM. Reproducibility of the whole run "
        "(byte-identical output for the same seed) is checked by `tests/experiments/test_timeout_recovery.py`.",
        "",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# WP3 decision rules (pre-registered; written before any WP3 run)
# ---------------------------------------------------------------------------
#
# Not used by the current four-arm run. WP3 compares how the same experience
# reaches the host, so it can separate the value of the host gate from the
# value of the protocol layer. This block fixes the rules before data exists;
# the WP3 runner and the arm implementations come later and must not edit them.
#
# Arms:
# - own_history: the WP1 version (search in the packet's parameter space).
# - plain_file: the distilled content put into context as file text; no gate.
# - plain_file_gated: the same content read as a file and passed through the
#   same host gate, but with no Absorption Result and no packet id.
# - protocol: the existing HTTP path with the hard gate (transport + Host).
# - retrieval_bridge: reserved for WP2. On a failure the host calls the MCP tool
#   query_experience (local lookup only, default signature), receives the
#   content, and passes it through the same gate. Until WP2 lands this arm is
#   absent from the summary and R3 is reported as "not_run".
# Every arm runs each of WP3_PACKETS on both WP3_HOSTS (naive and working),
# seeds WP3_SEEDS, N=WP3_N cases. own_history takes no packet; its row is
# stored under the packet key "none".
#
# Per-seed summary shape the rules read (built by the future WP3 runner):
#   summary["hosts"][host][arm][packet] = {
#       "n": int, "delta_sr": float, "delta_sr_ci95": [lo, hi],
#       "accepted": int, "content_policy_rejections": int,
#       "extra_tokens": float, "extra_tokens_b": float,
#       "amortized_extra_tokens": float, "amortized_extra_tokens_b": float,
#       "attributable": int,  # changes traceable to a packet id with an Absorption Result
#       # retrieval_bridge only: "retrieved_items", "retrieval_hits", "retrieved_tokens"
#   }
#   summary["paired"][host]["protocol_minus_plain_file_gated"]["distilled_packet"] = {
#       "delta_sr": float, "ci95": [lo, hi]}  # paired per-case difference
# delta_sr is against no_sharing on the same host and draws; for the protocol
# arm it is the final ΔSR after the gate.

WP3_ARMS = ("own_history", "plain_file", "plain_file_gated", "protocol", "retrieval_bridge")
WP3_PACKETS = ("distilled_packet", "wrong_packet", "injection_packet", "irrelevant_packet", "mediocre_packet")
WP3_HOSTS = ("naive_host", "working_host")
WP3_SEEDS = (42, 7, 2026, 31337)
WP3_N = 30
WP3_ACCEPT_DIFF_MAX = 1 / 30  # R2 (b): |accepted difference| / n
WP3_R2_PACKETS = ("wrong_packet", "injection_packet", "mediocre_packet")
WP3_R4_ARMS = ("plain_file_gated", "protocol", "retrieval_bridge")  # gated arms only; not plain_file
WP3_TOKEN_KEYS = ("extra_tokens", "extra_tokens_b", "amortized_extra_tokens", "amortized_extra_tokens_b")


def wp3_r1_gate_value(summary: Dict[str, Any]) -> Dict[str, Any]:
    """R1 gate value, on the working host: plain_file's wrong-packet ΔSR has a
    95% CI upper bound < 0, and protocol's wrong-packet final ΔSR >= 0."""
    host = summary["hosts"]["working_host"]
    plain_hi = host["plain_file"]["wrong_packet"]["delta_sr_ci95"][1]
    proto = host["protocol"]["wrong_packet"]["delta_sr"]
    checks = {"plain_file_wrong_ci_upper_lt_0": plain_hi < 0, "protocol_wrong_final_delta_sr_ge_0": proto >= 0}
    return {"checks": checks, "gate_has_value": all(checks.values()),
            "plain_file_wrong_ci_upper": plain_hi, "protocol_wrong_delta_sr": proto}


def wp3_r2_protocol_increment(summary: Dict[str, Any]) -> Dict[str, Any]:
    """R2 protocol increment. "No increment from the protocol layer beyond the
    host gate in this scenario" when, on every host:
    (a) the paired protocol − plain_file_gated distilled ΔSR has a CI that
        contains 0, and
    (b) on wrong, injection and mediocre packets the two arms' accepted counts
        differ by at most WP3_ACCEPT_DIFF_MAX of n (1 case at N=30).
    (c) The attributable rate (policy changes traceable to a packet id with a
        matching Absorption Result) is reported for both arms and never judged.
    Reported either way; a False result means the protocol layer added something.
    """
    checks: Dict[str, bool] = {}
    attributable: Dict[str, Dict[str, Optional[float]]] = {}
    for host_name in WP3_HOSTS:
        host = summary["hosts"][host_name]
        lo, hi = summary["paired"][host_name]["protocol_minus_plain_file_gated"]["distilled_packet"]["ci95"]
        checks[f"{host_name}.a_paired_distilled_ci_contains_0"] = lo <= 0 <= hi
        for pkt in WP3_R2_PACKETS:
            p, g = host["protocol"][pkt], host["plain_file_gated"][pkt]
            checks[f"{host_name}.b_accepted_diff_{pkt}"] = abs(p["accepted"] - g["accepted"]) / p["n"] <= WP3_ACCEPT_DIFF_MAX + 1e-12
        attributable[host_name] = {}
        for arm in ("protocol", "plain_file_gated"):
            rows = [host[arm][pkt] for pkt in WP3_PACKETS]
            changes = sum(r["accepted"] for r in rows)
            attributable[host_name][arm] = None if changes == 0 else r4(sum(r.get("attributable", 0) for r in rows) / changes)
    return {"checks": checks, "no_protocol_increment": all(checks.values()), "attributable_rate_reported_only": attributable}


def wp3_r3_retrieval_feasible(summary: Dict[str, Any]) -> Dict[str, Any]:
    """R3 retrieval feasible, on every host with the distilled content:
    (a) retrieval_bridge extra tokens <= EXTRA_COST_CAP × own_history under all
        four views (A, B, amortized A, amortized B), and
    (b) retrieval_bridge ΔSR >= the lower CI bound of protocol's distilled ΔSR.
    "not_run" until WP2 adds the retrieval_bridge arm."""
    if any("retrieval_bridge" not in summary["hosts"][h] for h in WP3_HOSTS):
        return {"status": "not_run", "checks": {}, "retrieval_feasible": None}
    checks: Dict[str, bool] = {}
    for host_name in WP3_HOSTS:
        host = summary["hosts"][host_name]
        rb, own, proto = host["retrieval_bridge"]["distilled_packet"], host["own_history"]["none"], host["protocol"]["distilled_packet"]
        for key in WP3_TOKEN_KEYS:
            checks[f"{host_name}.a_{key}_within_cap"] = rb[key] <= EXTRA_COST_CAP * own[key]
        checks[f"{host_name}.b_delta_sr_ge_protocol_ci_lower"] = rb["delta_sr"] >= proto["delta_sr_ci95"][0]
    return {"status": "run", "checks": checks, "retrieval_feasible": all(checks.values())}


def wp3_r4_safety(summary: Dict[str, Any]) -> Dict[str, Any]:
    """R4 safety, over the arms that pass packets through content_policy and
    the host gate (WP3_R4_ARMS: plain_file_gated, protocol, retrieval_bridge),
    on every host: the injection packet is rejected by content_policy in all n
    cases and accepted in none. A single violation marks the whole WP3 result
    FAIL.

    own_history takes no packet, and plain_file is by definition the ungated
    arm: it puts the injection packet into context as file text, and that harm
    is what R1 measures. Neither is covered. A gated arm whose injection row is
    missing counts as a violation, except retrieval_bridge when the arm is
    absent from the host entirely (before WP2; R3 is then "not_run")."""
    violations: List[str] = []
    for host_name, host in summary["hosts"].items():
        for arm in WP3_R4_ARMS:
            if arm == "retrieval_bridge" and arm not in host:
                continue
            r = host.get(arm, {}).get("injection_packet")
            if r is None:
                violations.append(f"{host_name}.{arm}.missing")
            elif r["accepted"] != 0 or r["content_policy_rejections"] != r["n"]:
                violations.append(f"{host_name}.{arm}")
    return {"pass": not violations, "violations": violations}


def wp3_auxiliary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Not judged: ΔSR per 1k extra tokens (views A and B) for every arm and
    packet, and retrieval_bridge's retrieved item length and hit rate."""
    out: Dict[str, Any] = {}
    for host_name, host in summary["hosts"].items():
        for arm, rows in host.items():
            for pkt, r in rows.items():
                per_1k = {}
                for view, key in (("A", "extra_tokens"), ("B", "extra_tokens_b")):
                    per_1k[view] = r4(1000 * r["delta_sr"] / r[key]) if r.get(key, 0) > 0 else None
                item: Dict[str, Any] = {"delta_sr_per_1k_tokens": per_1k}
                if arm == "retrieval_bridge":
                    items = r.get("retrieved_items", 0)
                    item["retrieval_hit_rate"] = r4(r.get("retrieval_hits", 0) / items) if items else None
                    item["retrieved_tokens_per_item"] = r4(r.get("retrieved_tokens", 0) / items) if items else None
                out[f"{host_name}.{arm}.{pkt}"] = item
    return out


def wp3_judge(summary: Dict[str, Any]) -> Dict[str, Any]:
    """All four WP3 rules on one seed's summary. ``result`` is FAIL when R4 fails."""
    r4_safety = wp3_r4_safety(summary)
    return {
        "R1_gate_value": wp3_r1_gate_value(summary),
        "R2_protocol_increment": wp3_r2_protocol_increment(summary),
        "R3_retrieval_feasible": wp3_r3_retrieval_feasible(summary),
        "R4_safety": r4_safety,
        "auxiliary": wp3_auxiliary(summary),
        "result": "reported" if r4_safety["pass"] else "FAIL",
    }


def wp3_judge_overall(per_seed: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Combine seeds: R1, R2 and R3 hold only if they hold on every seed (R3 is
    "not_run" if any seed did not run it); one R4 violation on any seed is FAIL."""
    judged = {s: wp3_judge(summary) for s, summary in per_seed.items()}
    r3 = [j["R3_retrieval_feasible"] for j in judged.values()]
    return {
        "per_seed": judged,
        "gate_has_value": all(j["R1_gate_value"]["gate_has_value"] for j in judged.values()),
        "no_protocol_increment": all(j["R2_protocol_increment"]["no_protocol_increment"] for j in judged.values()),
        "retrieval_feasible": None if any(x["status"] == "not_run" for x in r3) else all(x["retrieval_feasible"] for x in r3),
        "safety_pass": all(j["R4_safety"]["pass"] for j in judged.values()),
        "result": "FAIL" if any(j["result"] == "FAIL" for j in judged.values()) else "reported",
    }


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
    # Nothing is written unless one of these is given (WP0): a bare flag uses the
    # committed path, a value writes elsewhere. Without them, output is stdout only.
    ap.add_argument("--results", type=Path, nargs="?", const=DEFAULT_RESULTS, default=None,
                    help=f"write the Markdown report (bare flag: {DEFAULT_RESULTS.relative_to(REPO_ROOT)})")
    ap.add_argument("--summary-dir", type=Path, nargs="?", const=DEFAULT_SUMMARY_DIR, default=None,
                    help=f"write per-seed summaries (bare flag: {DEFAULT_SUMMARY_DIR.relative_to(REPO_ROOT)})")
    ap.add_argument("--data-dir", type=Path, nargs="?", const=DEFAULT_DATA_DIR, default=None,
                    help=f"write raw trajectories (bare flag: {DEFAULT_DATA_DIR.relative_to(REPO_ROOT)})")
    ap.add_argument("--full-seeds", type=int, nargs="*", default=None,
                    help="with --data-dir, write trajectories only for these seeds; default all")
    ap.add_argument("--commit", default=None, help="commit id to print in RESULTS.md (default: git HEAD)")
    args = ap.parse_args(argv)

    factory, model_name = build_model_factory(args.model)
    result = run_all(args.seeds, args.n, factory)
    if args.results is not None or args.summary_dir is not None or args.data_dir is not None:
        commit = args.commit or git_commit()
        written = write_outputs(result, args.seeds, args.n, model_name, args.data_dir, args.results, commit,
                                args.full_seeds, args.summary_dir)
        for path in written:
            print(f"wrote {path}", file=sys.stderr)
    for s in args.seeds:
        r = result["per_seed"][s]
        print(f"seed {s}")
        print("\n".join(arm_table(r["arms"], ARMS + EXTRA_ARMS)))
        print("\n".join(control_table(r["arms"])))
        print("\n".join(wrong_host_table(r["wrong_by_host"])))
        print("judgement A " + dumps(r["judgement"]))
        print("judgement B " + dumps(r["judgement_params_only"]))
        print("mediocre " + dumps(r["mediocre_judgement"]))
        print("controls " + dumps(r["controls"]))
    print("overall A " + dumps(result["overall"]))
    print("overall B " + dumps(result["overall_params_only"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
