"""WP3 runner: how the same experience reaches the host (non-normative).

The decision rules live in ``run.py`` (pre-registered, not edited here). This
module only produces the per-seed summary those rules read.

Arms, per the pre-registration:

- ``own_history``: the WP1 search in the packet's parameter space, from this
  host's starting policy; row key ``none`` (no packet).
- ``plain_file``: the packet content read as file text and applied with **no**
  gate, so R1 can measure what the advice does by itself.
- ``plain_file_gated``: the same file content through the host gate, with no
  packet id and no Absorption Result. It runs the same SPEC §2.1 content check
  (R4 covers it), and is charged the same resident-context cost as ``protocol``
  so the only difference between the two arms is the packet id + result.
- ``protocol``: the in-process host path a packet takes (version check, content
  policy, gate, Absorption Result, attribution). The HTTP binding around it is
  covered by ``tests/interop/test_transport.py`` and the conformance suite; WP3
  measures decisions, not sockets, so it does not open a server per case.
- ``retrieval_bridge``: the host asks the local corpus (``mcp_a2a.lookup``) for
  experience, gets the content of the best hit, and passes it through the same
  gate. The corpus for a row holds that row's packet only, so R3 measures the
  cost of the retrieval path, not the ranking quality of the lexical matcher.

Everything is derived from the seeds; no wall-clock time is written.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ai_oriented_a2a import scan_directives
from ai_oriented_a2a.recovery import parse_recovery_params, policy_from_params

from mcp_a2a import lookup

from .host import (
    Case,
    Host,
    approx_tokens,
    naive_policy,
    run_window,
    search_own_policy,
)
from .packets import control_packets, distilled_packet, mediocre_packet, working_policy
from .run import (
    ArmCase,
    bootstrap_ci,
    r4,
    summarize_arm,
    wp3_judge_overall,
    WP3_N,
    WP3_PACKETS,
    WP3_SEEDS,
    WP3_HOSTS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = Path(__file__).resolve().parent / "WP3_RESULTS.md"
DEFAULT_SUMMARY_DIR = Path(__file__).resolve().parent / "wp3_summaries"
MODEL_NAME = "deterministic-rule-host-v1"

#: What a host would ask the local corpus after this failure. Fixed before the
#: run; retrieval quality is not what WP3 measures.
FAILURE_QUERY = "tool call timeout throttled immediate retries stale partial results retry backoff"


# ---------------------------------------------------------------------------
# Hosts, baselines, and the packets under test
# ---------------------------------------------------------------------------


def start_policy(host_name: str):
    """The policy a host starts from: naive, or a receiver that already recovers."""
    return naive_policy() if host_name == "naive_host" else working_policy()


def _evaluate(policy, case: Case):
    return run_window(policy, case, "immediate") + run_window(policy, case, "delayed")


def _baseline(case: Case, host_name: str, model_factory: Callable[[], Any]) -> ArmCase:
    host = Host(case, model_factory())
    host.policy = start_policy(host_name)
    return ArmCase("no_sharing", case, _evaluate(host.policy, case), 0, 0, None, policy=host.policy.label())


def packets_for(seed: int) -> Dict[str, Dict[str, Any]]:
    """The five packets WP3 puts in front of a host, keyed by their row name."""
    controls = control_packets(seed)
    return {
        "distilled_packet": distilled_packet(seed),
        "wrong_packet": controls["wrong_packet"],
        "injection_packet": controls["injection_packet"],
        "irrelevant_packet": controls["irrelevant_packet"],
        "mediocre_packet": mediocre_packet(seed),
    }


def content_text(packet: Dict[str, Any]) -> str:
    return json.dumps(packet.get("content"), ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------


def arm_own_history(case: Case, host_name: str, model_factory: Callable[[], Any]) -> ArmCase:
    """WP1's own_history, from this host's starting policy."""
    host = Host(case, model_factory())
    host.policy = start_policy(host_name)
    search = search_own_policy(case, host.policy)
    explore_tokens = sum(e.tokens for e in search.episodes)
    explore_tokens_b = sum(e.tokens_b for e in search.episodes)
    explore_calls = sum(e.tool_calls for e in search.episodes)
    picked = policy_from_params(host.policy, search.params).label()
    if not search.params:
        return ArmCase(
            "own_history", case, _evaluate(host.policy, case), explore_tokens, explore_calls, None,
            "no change suggested by own history", host.policy.label(), strategy_parsed=False,
            overhead_tokens_b=explore_tokens_b, searched_policy=picked,
        )
    rec = host.gate(
        "own_history", search.params, search.evidence, 0, context_tokens=approx_tokens(search.params),
    )
    return ArmCase(
        "own_history", case, _evaluate(host.policy, case),
        explore_tokens + rec.isolation_tokens, explore_calls + rec.isolation_tool_calls,
        rec.status, rec.reason, host.policy.label(), rec.rollback_triggered, rec.rollback_ok,
        rec.isolation_dsr, True, overhead_tokens_b=explore_tokens_b + rec.isolation_tokens_b,
        searched_policy=picked,
    )


def arm_protocol(case: Case, packet: Dict[str, Any], host_name: str,
                 model_factory: Callable[[], Any]) -> Tuple[ArmCase, int]:
    """The packet path a host runs in-process. Returns (row, attributable)."""
    host = Host(case, model_factory())
    host.policy = start_policy(host_name)
    reply = host.handle_message(packet)
    rec = host.log[-1] if host.log else None
    metrics = (reply.get("effect_summary") or {}).get("metrics") or {}
    attributable = 1 if (
        reply.get("status") == "absorbed"
        and reply.get("packet_ref") == packet.get("id")
        and (rec is not None and rec.policy_after != rec.policy_before)
    ) else 0
    row = ArmCase(
        "protocol", case, _evaluate(host.policy, case),
        (rec.absorb_tokens + rec.isolation_tokens) if rec else 0,
        rec.isolation_tool_calls if rec else 0,
        reply.get("status"), reply.get("reason", ""), host.policy.label(),
        bool(rec and rec.rollback_triggered), rec.rollback_ok if rec else None,
        rec.isolation_dsr if rec and rec.params else None,
        bool(metrics.get("strategy_parsed")) if metrics else False,
        rec.model_calls if rec else 0, reply,
        overhead_tokens_b=(rec.absorb_tokens + rec.isolation_tokens_b) if rec else 0,
    )
    return row, attributable


def arm_plain_file(case: Case, packet: Dict[str, Any], host_name: str,
                   model_factory: Callable[[], Any], *, gated: bool) -> ArmCase:
    """The packet content as file text, with or without the host gate.

    Ungated is the counterfactual R1 compares against: same advice, no
    isolation, no rollback. Gated runs the §2.1 content check and the same gate
    as ``protocol``, but keeps no packet id and emits no Absorption Result.
    """
    arm = "plain_file_gated" if gated else "plain_file"
    text = content_text(packet)
    read_tokens = approx_tokens(text)
    params, evidence = parse_recovery_params(packet.get("content"))
    hits = scan_directives(packet.get("content"))

    host = Host(case, model_factory())
    host.policy = start_policy(host_name)
    # The ungated arm is the counterfactual of "no gate", which includes no
    # content policy; R4 therefore excludes it by design.
    if gated and hits:
        return ArmCase(
            arm, case, _evaluate(host.policy, case), read_tokens, 0, "rejected",
            f"content_policy: directive pattern(s) in content: {', '.join(hits)}",
            host.policy.label(), strategy_parsed=False, overhead_tokens_b=read_tokens,
        )
    if not params:
        return ArmCase(
            arm, case, _evaluate(host.policy, case), read_tokens, 0, "rejected",
            "no recovery parameters found in file content; local policy unchanged",
            host.policy.label(), strategy_parsed=False, overhead_tokens_b=read_tokens,
        )
    if not gated:
        policy = policy_from_params(host.policy, params)
        policy.context_tokens = host.policy.context_tokens + read_tokens
        policy.param_tokens = host.policy.param_tokens + approx_tokens(params)
        return ArmCase(
            arm, case, _evaluate(policy, case), read_tokens, 0, None, policy=policy.label(),
            strategy_parsed=True, overhead_tokens_b=read_tokens,
        )
    rec = host.gate("file", params, evidence, read_tokens, context_tokens=read_tokens)
    return ArmCase(
        arm, case, _evaluate(host.policy, case), read_tokens + rec.isolation_tokens,
        rec.isolation_tool_calls, rec.status, rec.reason, host.policy.label(),
        rec.rollback_triggered, rec.rollback_ok, rec.isolation_dsr, True,
        overhead_tokens_b=read_tokens + rec.isolation_tokens_b,
    )


def _corpus_for(packet: Dict[str, Any]) -> Dict[str, Any]:
    """A one-packet corpus. The row's experience is what the host has locally."""
    directory = Path(tempfile.mkdtemp(prefix="wp3-corpus-"))
    try:
        (directory / f"{packet['id']}.json").write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
        return lookup.query_packets(FAILURE_QUERY, directory=directory, limit=1, max_tokens=2000)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def arm_retrieval(case: Case, packet: Dict[str, Any], host_name: str, model_factory: Callable[[], Any],
                  result: Dict[str, Any]) -> Tuple[ArmCase, Dict[str, int]]:
    """The local-corpus path: query, take the hit, gate it. Returns (row, stats)."""
    stats = {
        "retrieved_items": len(result["items"]),
        "retrieval_hits": 1 if result["items"] else 0,
        "retrieved_tokens": result["tokens"],
    }
    host = Host(case, model_factory())
    host.policy = start_policy(host_name)
    if result["blocked"]:
        reason = result["blocked"][0]["reason"]
        return ArmCase(
            "retrieval_bridge", case, _evaluate(host.policy, case), result["tokens"], 0, "rejected",
            reason, host.policy.label(), strategy_parsed=False, overhead_tokens_b=result["tokens"],
        ), stats
    if not result["items"]:
        return ArmCase(
            "retrieval_bridge", case, _evaluate(host.policy, case), result["tokens"], 0, "rejected",
            "no retrieval hit for the failure; local policy unchanged", host.policy.label(),
            strategy_parsed=False, overhead_tokens_b=result["tokens"],
        ), stats
    item = result["items"][0]
    params, evidence = parse_recovery_params(item.get("content"))
    if not params:
        return ArmCase(
            "retrieval_bridge", case, _evaluate(host.policy, case), result["tokens"], 0, "rejected",
            "no recovery parameters found in the retrieved content; local policy unchanged",
            host.policy.label(), strategy_parsed=False, overhead_tokens_b=result["tokens"],
        ), stats
    rec = host.gate("retrieval", params, evidence, result["tokens"], context_tokens=result["tokens"])
    return ArmCase(
        "retrieval_bridge", case, _evaluate(host.policy, case), result["tokens"] + rec.isolation_tokens,
        rec.isolation_tool_calls, rec.status, rec.reason, host.policy.label(),
        rec.rollback_triggered, rec.rollback_ok, rec.isolation_dsr, True,
        overhead_tokens_b=result["tokens"] + rec.isolation_tokens_b,
    ), stats


# ---------------------------------------------------------------------------
# One seed
# ---------------------------------------------------------------------------


def _amortized(summary: Dict[str, Any], n: int) -> Tuple[float, float]:
    """One-time cost shared across N cases + the per-case evaluation extra."""
    one_time = summary.get("one_time_tokens") or 0.0
    one_time_b = summary.get("one_time_tokens_b") or 0.0
    return (
        r4(one_time / n + (summary.get("eval_extra_tokens") or 0.0)),
        r4(one_time_b / n + (summary.get("eval_extra_tokens_b") or 0.0)),
    )


def run_host_seed(seed: int, host_name: str, n: int, model_factory: Callable[[], Any]) -> Dict[str, Any]:
    cases = [Case(seed, i) for i in range(1, n + 1)]
    base = [_baseline(case, host_name, model_factory) for case in cases]
    packets = packets_for(seed)
    corpora = {name: _corpus_for(packet) for name, packet in packets.items()}

    rows: Dict[str, Dict[str, List[ArmCase]]] = {}
    for arm in ("own_history", "plain_file", "plain_file_gated", "protocol", "retrieval_bridge"):
        # own_history takes no packet: its single row lives under the key "none".
        rows[arm] = {"none": []} if arm == "own_history" else {pkt: [] for pkt in WP3_PACKETS}
    attributable: Dict[str, Dict[str, int]] = {arm: {pkt: 0 for pkt in WP3_PACKETS} for arm in rows}
    retrieval: Dict[str, Dict[str, int]] = {
        pkt: {"retrieved_items": 0, "retrieval_hits": 0, "retrieved_tokens": 0} for pkt in WP3_PACKETS
    }

    for case in cases:
        rows["own_history"]["none"].append(arm_own_history(case, host_name, model_factory))
        for name in WP3_PACKETS:
            packet = packets[name]
            row, at = arm_protocol(case, packet, host_name, model_factory)
            rows["protocol"][name].append(row)
            attributable["protocol"][name] += at
            rows["plain_file"][name].append(arm_plain_file(case, packet, host_name, model_factory, gated=False))
            rows["plain_file_gated"][name].append(arm_plain_file(case, packet, host_name, model_factory, gated=True))
            rrow, stats = arm_retrieval(case, packet, host_name, model_factory, corpora[name])
            rows["retrieval_bridge"][name].append(rrow)
            for key, value in stats.items():
                retrieval[name][key] += value

    paired_diffs = [
        proto.sr - gated.sr
        for proto, gated in zip(rows["protocol"]["distilled_packet"], rows["plain_file_gated"]["distilled_packet"])
    ]

    summaries: Dict[str, Dict[str, Any]] = {}
    for arm, per_packet in rows.items():
        summaries[arm] = {}
        for pkt, arm_rows in per_packet.items():
            summary = summarize_arm(seed, f"{host_name}.{arm}.{pkt}", arm_rows, base)
            summary["attributable"] = attributable.get(arm, {}).get(pkt, 0)
            if arm == "retrieval_bridge":
                summary.update(retrieval[pkt])
            am_a, am_b = _amortized(summary, n)
            summary["amortized_extra_tokens"] = am_a
            summary["amortized_extra_tokens_b"] = am_b
            summary["paired_distilled_diffs"] = paired_diffs if arm == "protocol" else None
            summaries[arm][pkt] = summary
    return summaries


def run_seed(seed: int, n: int, model_factory: Callable[[], Any]) -> Dict[str, Any]:
    hosts = {host: run_host_seed(seed, host, n, model_factory) for host in WP3_HOSTS}
    paired: Dict[str, Any] = {}
    for host in WP3_HOSTS:
        diffs = hosts[host]["protocol"]["distilled_packet"].get("paired_distilled_diffs") or []
        lo, hi = bootstrap_ci(diffs, (seed, host, "protocol_minus_plain_file_gated"))
        paired[host] = {"protocol_minus_plain_file_gated": {"distilled_packet": {
            "delta_sr": r4(sum(diffs) / len(diffs)) if diffs else 0.0,
            "ci95": [r4(lo), r4(hi)],
        }}}
    return {"seed": seed, "n": n, "hosts": hosts, "paired": paired}


def run_all(seeds: Sequence[int], n: int, model_factory: Callable[[], Any]) -> Dict[str, Any]:
    per_seed = {seed: run_seed(seed, n, model_factory) for seed in seeds}
    return {"per_seed": per_seed, "overall": wp3_judge_overall(per_seed)}


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _fmt(x: Optional[float], signed: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.3f}" if signed else f"{x:.3f}"


def results_markdown(result: Dict[str, Any], seeds: Sequence[int], n: int, commit: str) -> str:
    lines = [
        "# WP3: how the same experience reaches the host",
        "",
        "Generated by `experiments/timeout_recovery/wp3.py`. Do not edit by hand; rerun the command.",
        "Non-normative. The tool is simulated (see `host.py`); the numbers are about this simulator and host.",
        "",
        "## Run",
        "",
        f"- Command: `python -m experiments.timeout_recovery.wp3 --seeds {' '.join(map(str, seeds))} --n {n} --results --summary-dir`",
        f"- Generated at commit: `{commit}`",
        f"- Environment: Python {sys.version.split()[0]}",
        f"- Strategy model: `{MODEL_NAME}`",
        f"- Cases: N={n} per arm per seed; 3 immediate + 3 delayed episodes per case, paired across arms",
        "- ΔSR is per case against the same host's `no_sharing` on the same draws; 95% CI: paired bootstrap, B=2000",
        "- `protocol` runs in-process (version, content policy, gate, Absorption Result); the HTTP binding is covered by the transport tests",
        "- `plain_file_gated` is charged the same resident-context cost as `protocol`, so the arms differ only in packet id + result",
        "- `retrieval_bridge` reads a one-packet local corpus per row, so it measures the retrieval path's cost, not ranking quality",
        "",
    ]
    for seed in seeds:
        summary = result["per_seed"][seed]
        lines += [f"## Seed {seed}", ""]
        for host in WP3_HOSTS:
            lines += [f"### {host}", "",
                      "| arm | packet | SR | ΔSR | 95% CI | extra tokens A | extra tokens B | accepted | content_policy | attributable |",
                      "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |"]
            for arm in ("own_history", "plain_file", "plain_file_gated", "protocol", "retrieval_bridge"):
                for pkt in (("none",) if arm == "own_history" else WP3_PACKETS):
                    row = summary["hosts"][host][arm][pkt]
                    lines.append(
                        f"| {arm} | {pkt} | {_fmt(row['sr'])} | {_fmt(row['delta_sr'], True)} | "
                        f"[{_fmt(row['delta_sr_ci95'][0], True)}, {_fmt(row['delta_sr_ci95'][1], True)}] | "
                        f"{_fmt(row['extra_tokens'])} | {_fmt(row['extra_tokens_b'])} | "
                        f"{row['accepted']}/{row['n']} | {row['content_policy_rejections']} | {row.get('attributable')} |"
                    )
            lines.append("")
        lines += ["Paired `protocol − plain_file_gated` on `distilled_packet`:", ""]
        for host in WP3_HOSTS:
            row = summary["paired"][host]["protocol_minus_plain_file_gated"]["distilled_packet"]
            lines.append(f"- {host}: ΔSR {_fmt(row['delta_sr'], True)}, 95% CI [{_fmt(row['ci95'][0], True)}, {_fmt(row['ci95'][1], True)}]")
        lines.append("")

    overall = result["overall"]
    lines += ["## Verdicts (fixed rules, `run.py`)", ""]
    for seed in seeds:
        judged = overall["per_seed"][seed]
        r1, r2 = judged["R1_gate_value"], judged["R2_protocol_increment"]
        r3, r4_ = judged["R3_retrieval_feasible"], judged["R4_safety"]
        lines += [
            f"### Seed {seed}",
            "",
            f"- R1 gate value: {'pass' if r1['gate_has_value'] else 'FAIL'}"
            f" (ungated wrong-packet CI upper {_fmt(r1['plain_file_wrong_ci_upper'], True)}, protocol final ΔSR {_fmt(r1['protocol_wrong_delta_sr'], True)})",
            f"- R2 protocol increment: **{r2['increment']}** (margin ±{r2['margin']}); per host "
            + ", ".join(f"{h}={v}" for h, v in r2["per_host"].items()),
            f"- R2 accepted-count checks: {'pass' if all(v for k, v in r2['checks'].items() if '.b_' in k) else 'FAIL'}",
            f"- R3 retrieval feasible: {r3.get('retrieval_feasible')} (status {r3.get('status')})",
            f"- R4 safety: {'pass' if r4_['pass'] else 'FAIL ' + str(r4_['violations'])}",
            "",
        ]
    lines += [
        "## Overall",
        "",
        f"- gate_has_value: {overall['gate_has_value']}",
        f"- protocol_increment: **{overall['protocol_increment']}**",
        f"- retrieval_feasible: {overall['retrieval_feasible']}",
        f"- safety_pass: {overall['safety_pass']}",
        f"- result: {overall['result']}",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_model_factory(kind: str) -> Tuple[Callable[[], Any], str]:
    from .run import build_model_factory as _build

    return _build(kind)


def git_commit() -> str:
    from .run import git_commit as _git_commit

    return _git_commit()


def write_outputs(result: Dict[str, Any], seeds: Sequence[int], n: int, results: Optional[Path],
                  summary_dir: Optional[Path], commit: str) -> None:
    if results is not None:
        results.parent.mkdir(parents=True, exist_ok=True)
        results.write_text(results_markdown(result, seeds, n, commit), encoding="utf-8")
    if summary_dir is not None:
        summary_dir.mkdir(parents=True, exist_ok=True)
        for seed in seeds:
            payload = {
                "seed": seed,
                "n": n,
                "hosts": result["per_seed"][seed]["hosts"],
                "paired": result["per_seed"][seed]["paired"],
                "judge": result["overall"]["per_seed"][seed],
            }
            (summary_dir / f"wp3-seed-{seed}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(WP3_SEEDS))
    parser.add_argument("--n", type=int, default=WP3_N)
    parser.add_argument("--model", choices=("rule", "llm"), default="rule")
    parser.add_argument("--results", type=Path, nargs="?", const=DEFAULT_RESULTS, default=None)
    parser.add_argument("--summary-dir", type=Path, nargs="?", const=DEFAULT_SUMMARY_DIR, default=None)
    parser.add_argument("--commit", default=None)
    args = parser.parse_args(argv)

    model_factory, model_name = build_model_factory(args.model)
    # Print before the run, for the same reason as run.py: an output file must
    # say which strategy model produced it.
    print(f"strategy model: {model_name}")
    result = run_all(args.seeds, args.n, model_factory)
    write_outputs(result, args.seeds, args.n, args.results, args.summary_dir, args.commit or git_commit())

    overall = result["overall"]
    print(json.dumps({
        "model": model_name,
        "seeds": list(args.seeds),
        "n": args.n,
        "gate_has_value": overall["gate_has_value"],
        "protocol_increment": overall["protocol_increment"],
        "retrieval_feasible": overall["retrieval_feasible"],
        "safety_pass": overall["safety_pass"],
        "result": overall["result"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
