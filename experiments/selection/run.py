"""Packet-selection experiment runner (PREREG_SELECTION, experiment 1; non-normative).

Implements ``docs/PREREG_SELECTION.md`` as merged in main at
``75acb59262cba1ae4439430d140653835641359f`` (file content of commit
``c04e5030bbbe5525899d8fb5bca2d3b41c53b1f5``). Rules, N and delta are frozen
there and are not edited here.

Default: the registered main run (seeds 42, 7, 2026, 31337 x 40 runs x 200
tasks, both hosts, all arms), printed to stdout. Nothing is written unless
``--results`` / ``--summary-dir`` is given (WP0 convention): a bare flag uses
the committed path, a value writes elsewhere. ``--pilot`` runs the §13 pilot
(seed 42, run indices 100-101, 50 tasks). Everything is derived from the seeds;
no wall-clock time is written.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .analysis import (
    HALF_WIDTH_FLAG,
    SR_CONTRASTS,
    analyse,
    jsonable,
    rule_of,
)
from .sim import (
    ARM_ORDER,
    ARMS,
    DELTA,
    HISTORY_WEIGHT,
    HOSTS,
    PILOT_RUNS,
    PILOT_SEED,
    PILOT_TASKS,
    PRIMARY_HOST,
    RUNS,
    SEEDS,
    SLOT_TASKS,
    TASKS,
    simulate_run,
)
from .pool import PRIOR_FLIP_P

REPO_ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DEFAULT_RESULTS = HERE / "SELECTION_RESULTS.md"
DEFAULT_SUMMARY_DIR = HERE / "summaries"
PREREG_MERGE_COMMIT = "75acb59262cba1ae4439430d140653835641359f"
PREREG_FILE_COMMIT = "c04e5030bbbe5525899d8fb5bca2d3b41c53b1f5"
MODEL_NAME = "deterministic-rule-host-v1"


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _one(args: Tuple[int, int, int]) -> Dict[str, Any]:
    seed, run, n_tasks = args
    return simulate_run(seed, run, n_tasks)


def run_all(seeds: Sequence[int], runs: Sequence[int], n_tasks: int, jobs: int = 1) -> List[Dict[str, Any]]:
    """Simulate every (seed, run); the output order and content do not depend on ``jobs``."""
    work = [(s, r, n_tasks) for s in seeds for r in runs]
    if jobs <= 1:
        return [_one(w) for w in work]
    with Pool(jobs) as pool:
        return list(pool.imap(_one, work, chunksize=1))


def run_rows(runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compact per-run rows for ``runs.jsonl``: one row per (seed, run, host).

    Every list is in arm order A, B, C, D1, D2, V1, V2. ``plateau_successes``
    are out of ``plateau_episodes`` (300 = 50 tasks x 6 episodes);
    ``window_successes`` are the eight 25-task windows (out of 150 each);
    ``slots`` are ``family:status`` initials per slot (e/m/h : a/r).
    """
    rows = []
    for r in runs:
        for host, arms in r["hosts"].items():
            es = [arms[label] for label in ARM_ORDER]
            rows.append({
                "seed": r["seed"], "run": r["run"], "host": host, "arms": list(ARM_ORDER),
                "plateau_episodes": es[0]["plateau_den"],
                "plateau_successes": [e["plateau_num"] for e in es],
                "window_successes": [e["window_successes"] for e in es],
                "tasks_to_plateau": [e["tasks_to_plateau"] for e in es],
                "plateau_not_reached": [e["plateau_not_reached"] for e in es],
                "tokens_to_plateau": [e["tokens_to_plateau"] for e in es],
                "total_tokens": [e["total_tokens"] for e in es],
                "selection_tokens": [e["selection_tokens"] for e in es],
                "slots": [" ".join(e["slot_codes"]) for e in es],
            })
    return rows


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _f(x: Any, signed: bool = False, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    v = float(x) + 0.0
    return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"


def _ci(c: Dict[str, Any]) -> str:
    return f"{_f(c['estimate'], True)} [{_f(c['lo'], True)}, {_f(c['hi'], True)}]"


def _int(x: Any) -> str:
    return "n/a" if x is None else f"{float(x):,.0f}"


EXCHANGEABILITY = (
    "Exchangeability gate (§3, §15.1): **not met**. No implementation outside this project passes "
    "`tests/conformance/` and reads the selection envelope fields yet, so the envelope is a "
    "single-project format, not yet an interoperable protocol."
)
SCENARIO_NOTE = (
    "Scope: one simulated tool (`web_search` timeout recovery) with a rule-parser receiver "
    "(`deterministic-rule-host-v1`); synthetic peers and packets."
)

IMPLEMENTATION_NOTES = (
    "Bootstrap: §12 asks for a percentile bootstrap stratified by seed and seeded with "
    "`stable_int(\"selection-bootstrap\", host, contrast)`. `timeout_recovery.run.bootstrap_ci` is not "
    "stratified and uses another seed prefix, so `analysis.stratified_bootstrap` implements §12 with the "
    "same B (`BOOTSTRAP_B` = 2000) and the same percentile indices. `summarize_arm` is per case and is not "
    "used; the run-level summaries are in `analysis.arm_summary`. `Host`, its gate, `RuleStrategyModel`, "
    "`run_window`, `working_policy` and `stable_int` are reused unchanged.",
    "Task t of a run is `Case(stable_int(\"selection\", seed, run), t)`; its 3 `immediate` + 3 `delayed` "
    "episodes give the task SR. At a slot the gate's isolation runs on the slot task's case against the "
    "current policy.",
    "Prior receivers (§6.2): receiver j of packet p is `Case(stable_int(\"prior\", seed, run, p, j), 0)` "
    "for j = 0..m-1, a fresh `naive_host` that receives the packet through `Host.handle_message`. m and the "
    "phi = 0.15 flips come from `stable_int(\"prior-noise\", seed, run, p)`.",
    "Honest `verification_claim.value` (§6.2): the mean SR (6 episodes per case) of the policy the packet "
    "states, applied to a `naive_host` start policy, over 30 further prior-stream cases j = 1000..1029; "
    "`repro.seed` is the registered seed. The dishonest peer (§6.3) draws from `stable_int(\"dishonest\", "
    "seed, run, p)` and runs nothing.",
    "Pool (§6.1): drawn from `stable_int(\"pool\", seed, run)`; packet ids are opaque hashes that reveal "
    "neither peer nor family; every packet has `type = failure_mode` and `absorption.method = "
    "prompt_injection`. Ground truth (family, author) is kept beside the packet and follows the content "
    "under the V2 shuffle.",
    "Own Absorption Results (§7.2): status `absorbed` counts as `own_absorbed`; every other status counts "
    "as `own_not_absorbed` (in this host a gated-out change is reported as `rejected` after its rollback). "
    "A result is attributed to the `provenance.source_ref` of the packet as delivered (shuffled in V2) only "
    "when its `packet_ref` equals the packet id.",
    "`claim_selection` (§7.3): ties in `value` and the order of ineligible packets use the tie RNG "
    "`stable_int(\"tie\", seed, run, slot)`; in this pool every packet is eligible.",
    "Tokens: task tokens are the six evaluation episodes, including the resident absorbed context, as in "
    "`host.py`. Slot tokens are the absorption block (or file text for B) plus both isolation windows plus "
    "the post-rollback replay. Selection reading (§7.4) is the sum of `approx_tokens` of each untried "
    "packet's envelope (every field except `content`) at each slot, for D1, D2 and V2 only.",
    "V1 and V2 are registered on the primary contrast on `naive_host` (§11); their outcomes gate every "
    "table, including the secondary tables on `working_host`. V1/V2 computed on `working_host` are "
    "reported as information only.",
    "\"CI inside [-delta, +delta]\" (R2, V2) is inclusive at the bounds. All rule comparisons use exact "
    "rational arithmetic on success counts.",
    "Attribution rate: a policy change is traceable when an Absorption Result with `status = absorbed` "
    "and `packet_ref` equal to the delivered packet id exists. B has no packet id and no Absorption "
    "Result, so its rate is 0 by construction.",
    "Pilot (§13): with 50 tasks only slots T1 and T26 exist, and the plateau window is the last 50 tasks, "
    "i.e. the whole pilot. The pilot is a pipeline check only.",
    "Run log: after the pilot, the main run was executed once to `/tmp` and then regenerated into the "
    "committed files. Between the two, only report formatting changed (B − A is shown as information "
    "because the §12 labels are worded for selection contrasts; `runs.jsonl` was made compact). No "
    "simulation, selection, endpoint or rule code changed, and every number is identical.",
)


def _arm_table(host_res: Dict[str, Any]) -> List[str]:
    lines = [
        "| Label | Arm | plateau_SR | AUC | tasks_to_plateau | not reached | tokens_to_plateau | total tokens | selection tokens | runs with an effective packet absorbed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label in ARM_ORDER:
        a = host_res["arms"][label]
        lines.append(
            f"| {label} | `{a['name']}` | {_f(a['plateau_sr'])} | {_f(a['auc'])} | {_f(a['tasks_to_plateau'], nd=1)} | "
            f"{a['plateau_not_reached']} | {_int(a['tokens_to_plateau'])} | {_int(a['total_tokens'])} | "
            f"{_int(a['selection_tokens'])} | {a['runs_with_effective_absorbed']}/{a['runs']} |"
        )
    return lines


def _absorption_table(host_res: Dict[str, Any]) -> List[str]:
    lines = [
        "| Label | harmful selected | harmful absorbed | harmful rolled back | mediocre selected | mediocre absorbed | mediocre rolled back | effective selected | effective absorbed | slots before first effective absorbed (mean, runs that found one) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label in ARM_ORDER:
        a = host_res["arms"][label]
        ab = a["absorption"]

        def c(fam: str, k: str) -> str:
            return f"{ab[fam][k]['total']} ({_f(ab[fam][k]['per_run'], nd=2)}/run)"

        lines.append(
            f"| {label} | {c('harmful', 'selected')} | {c('harmful', 'absorbed')} | {c('harmful', 'rolled_back')} | "
            f"{c('mediocre', 'selected')} | {c('mediocre', 'absorbed')} | {c('mediocre', 'rolled_back')} | "
            f"{c('effective', 'selected')} | {c('effective', 'absorbed')} | {_f(a['slots_before_first_effective_mean'], nd=2)} |"
        )
    return lines


def _contrast_table(host_res: Dict[str, Any], host: str) -> List[str]:
    lines = [
        "| Contrast | Role | Estimate [95% CI] | Half-width | Per-seed means (42 / 7 / 2026 / 31337) | Rule | Label |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, _a, _b, role in SR_CONTRASTS:
        c = host_res["contrasts"][name]
        if host != PRIMARY_HOST and role.startswith("primary"):
            role = "secondary"
        if host != PRIMARY_HOST and role.startswith("validity"):
            role = role.replace("validity", "information:")
        per_seed = " / ".join(_f(c["per_seed"].get(str(s)), True) for s in SEEDS if str(s) in c["per_seed"])
        if name in host_res["verdicts"]:
            v = host_res["verdicts"][name]
            rule, label = v["rule"], v["headline"]
        else:
            rule, label = rule_of(c["estimate"], c["lo"], c["hi"])
            rule, label = f"({rule})", "not a selection contrast; rule shown for information, the §12 labels are worded for D1 - C and D2 - C"
        lines.append(f"| {name} | {role} | {_ci(c)} | {_f(c['half_width'])} | {per_seed} | {rule} | {label} |")
    return lines


def _token_table(host_res: Dict[str, Any]) -> List[str]:
    lines = ["| Contrast | Field | Estimate [95% CI] (tokens per run) |", "| --- | --- | --- |"]
    for key, c in host_res["token_contrasts"].items():
        lines.append(f"| {c['contrast']} | {c['field']} | {_int(c['estimate'])} [{_int(c['lo'])}, {_int(c['hi'])}] |")
    return lines


def _process_table(host_res: Dict[str, Any]) -> List[str]:
    lines = ["| Label | policy changes | attributed | attribution rate | rollbacks | rollback success | content_policy rejections of pool packets |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for label in ARM_ORDER:
        a = host_res["arms"][label]
        lines.append(
            f"| {label} | {a['policy_changes']} | {a['attributed_changes']} | {_f(a['attribution_rate'])} | "
            f"{a['rollbacks']} | {_f(a['rollback_success_rate'])} | {a['content_policy_rejections']} |"
        )
    return lines


def results_markdown(result: Dict[str, Any], pilot: Optional[Dict[str, Any]], seeds: Sequence[int],
                     runs: Sequence[int], n_tasks: int, commit: str) -> str:
    h = result["headline"]
    v = h["verdict"]
    prim = result["hosts"][PRIMARY_HOST]
    d1c = prim["contrasts"]["D1-C"]
    lines: List[str] = [
        "# Packet-selection experiment: results (experiment 1)",
        "",
        "> Generated by `python -m experiments.selection.run --results --summary-dir`. Do not hand-edit.",
        "> Non-normative. Rules: [`docs/PREREG_SELECTION.md`](../../docs/PREREG_SELECTION.md), frozen at its",
        f"> merge into main (`{PREREG_MERGE_COMMIT}`; file content of `{PREREG_FILE_COMMIT}`).",
        "> **Amendments: none.** No rule, N, delta or planning value was changed after the merge.",
        "",
        f"Runner base commit: `{commit}`. Strategy model: `{MODEL_NAME}`.",
        f"Sample: seeds {', '.join(str(s) for s in seeds)} × runs {runs[0]}–{runs[-1]} ({len(seeds) * len(runs)} runs) × "
        f"{n_tasks} tasks, hosts `naive_host` (primary) and `working_host` (secondary), arms "
        f"{', '.join(ARM_ORDER)}. Slots at tasks {', '.join('T' + str(t) for t in SLOT_TASKS)}; plateau window "
        f"T151–T200; delta = {float(DELTA):.2f}; history weight {HISTORY_WEIGHT}; reporting noise phi = {PRIOR_FLIP_P}; "
        "paired percentile bootstrap over runs, stratified by seed, B = 2000.",
        "",
        "## Headline (primary contrast D1 − C on `naive_host`)",
        "",
        f"- **Verdict: `{v['headline']}`** (rule {v['rule']}).",
    ]
    if v["reason"]:
        lines.append(f"- Reason: {v['reason']}. Rule table result, information only: `{v['information_label']}`.")
    lines += [
        f"- D1 − C plateau_SR: **{_ci(d1c)}**, realised half-width {_f(h['half_width'])}.",
        f"- Half-width flag (> {HALF_WIDTH_FLAG}): "
        + ("**triggered: the run was noisier than planned.**" if h["noisier_than_planned"] else "not triggered (the run was not noisier than planned)."),
        f"- V1 content-stub check (C − V1 lower bound > 0): **{h['v1']}**, C − V1 = {_ci(prim['contrasts']['C-V1'])}.",
        f"- V2 envelope-shuffle falsification (V2 − C inside ±0.10 and containing 0): **{h['v2']}**, "
        f"V2 − C = {_ci(prim['contrasts']['V2-C'])}.",
        f"- Harmful (dishonest-peer) absorption, `naive_host`: "
        + ", ".join(f"{lab} {prim['arms'][lab]['absorption']['harmful']['absorbed']['total']}" for lab in ARM_ORDER)
        + " (absorbed packets in total over all runs; selection counts below).",
        f"- {EXCHANGEABILITY}",
        f"- {SCENARIO_NOTE}",
        "",
    ]
    if v["rule"] == "R1b":
        lines += ["The estimate is positive but below the margin. Per §12 this is not support for the envelope.", ""]
    for host in HOSTS:
        res = result["hosts"][host]
        role = "primary" if host == PRIMARY_HOST else "secondary"
        lines += [
            f"## `{host}` ({role})",
            "",
            "### Contrasts (plateau_SR, paired per run)",
            "",
            *_contrast_table(res, host),
            "",
        ]
        if host != PRIMARY_HOST:
            lines += [f"V1/V2 computed on this host (information only, the registered checks are on `naive_host`): "
                      f"V1 {res['v1_on_this_host']}, V2 {res['v2_on_this_host']}. No headline statement rests on this host.", ""]
        lines += [
            "### Arms",
            "",
            *_arm_table(res),
            "",
            "### Absorption by packet family (totals over runs)",
            "",
            *_absorption_table(res),
            "",
            "### Token contrasts",
            "",
            *_token_table(res),
            "",
            "### Process metrics (not capability)",
            "",
            *_process_table(res),
            "",
        ]
    if pilot is not None:
        ph = pilot["headline"]
        pp = pilot["hosts"][PRIMARY_HOST]["contrasts"]
        lines += [
            "## Pilot (§13; not part of the analysis)",
            "",
            f"Seed {PILOT_SEED}, run indices {PILOT_RUNS[0]}–{PILOT_RUNS[-1]}, {PILOT_TASKS} tasks, both hosts, all arms. "
            "The pipeline ran end to end and V1/V2 computed "
            f"(pilot C − V1 {_ci(pp['C-V1'])}, V2 − C {_ci(pp['V2-C'])}, D1 − C {_ci(pp['D1-C'])}; two runs, so these "
            "numbers carry no information). No rule, N, delta or planning value was changed after the pilot. "
            "Bugs found in the pilot: none.",
            "",
        ]
    lines += [
        "## Implementation notes",
        "",
        "Choices the pre-registration leaves open, fixed in code before the main run (none changes a rule):",
        "",
        *[f"{i}. {note}" for i, note in enumerate(IMPLEMENTATION_NOTES, 1)],
        "",
        "## Wording (§14)",
        "",
        "`plateau_SR` is the mean task SR over T151–T200. `D1 − C` is the increment from using envelope fields "
        "to select packets under this scenario. An R2 result reads `equivalent within ±0.10 ΔSR`; R4 reads "
        "`undetermined`. Only an R1a result may be stated as support for the envelope. The attribution rate "
        "is a process metric.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "python -m experiments.selection.run --jobs 8                      # main run, stdout only",
        "python -m experiments.selection.run --pilot                       # pilot, stdout only",
        "python -m experiments.selection.run --jobs 8 --results --summary-dir   # regenerate committed files",
        "```",
        "",
    ]
    return "\n".join(lines)


def summary_json(result: Dict[str, Any], seeds: Sequence[int], runs: Sequence[int], n_tasks: int, commit: str) -> str:
    doc = {
        "experiment": "packet-selection (PREREG_SELECTION experiment 1)",
        "prereg_merge_commit": PREREG_MERGE_COMMIT,
        "prereg_file_commit": PREREG_FILE_COMMIT,
        "runner_base_commit": commit,
        "model": MODEL_NAME,
        "seeds": list(seeds),
        "runs": [runs[0], runs[-1]],
        "tasks": n_tasks,
        "delta": float(DELTA),
        "result": jsonable(result),
    }
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:  # pragma: no cover - no git
        return "unknown"


def _write(path: Path, text: str, written: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    written[str(path)] = text


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    ap.add_argument("--runs", type=int, default=RUNS, help="runs per seed (registered: 40)")
    ap.add_argument("--run-start", type=int, default=0, help="first run index (registered: 0)")
    ap.add_argument("--tasks", type=int, default=TASKS, help="tasks per run (registered: 200)")
    ap.add_argument("--pilot", action="store_true", help="the §13 pilot: seed 42, runs 100-101, 50 tasks")
    ap.add_argument("--jobs", type=int, default=1, help="worker processes (results do not depend on it)")
    ap.add_argument("--results", type=Path, nargs="?", const=DEFAULT_RESULTS, default=None,
                    help=f"write the Markdown report (bare flag: {DEFAULT_RESULTS.relative_to(REPO_ROOT)})")
    ap.add_argument("--summary-dir", type=Path, nargs="?", const=DEFAULT_SUMMARY_DIR, default=None,
                    help=f"write summary.json and runs.jsonl (bare flag: {DEFAULT_SUMMARY_DIR.relative_to(REPO_ROOT)})")
    ap.add_argument("--commit", default=None, help="commit id to print in the report (default: git HEAD)")
    args = ap.parse_args(argv)

    if args.pilot:
        seeds, runs, n_tasks = [PILOT_SEED], list(PILOT_RUNS), PILOT_TASKS
    else:
        seeds, runs, n_tasks = list(args.seeds), list(range(args.run_start, args.run_start + args.runs)), args.tasks
    print(f"strategy model: {MODEL_NAME}")
    raw = run_all(seeds, runs, n_tasks, args.jobs)
    result = analyse(raw)
    pilot = None
    if args.results is not None and not args.pilot:
        pilot = analyse(run_all([PILOT_SEED], list(PILOT_RUNS), PILOT_TASKS, args.jobs))
    if args.results is not None or args.summary_dir is not None:
        commit = args.commit or git_commit()
        written: Dict[str, str] = {}
        if args.results is not None:
            _write(args.results, results_markdown(result, pilot, seeds, runs, n_tasks, commit), written)
        if args.summary_dir is not None:
            _write(args.summary_dir / "summary.json", summary_json(result, seeds, runs, n_tasks, commit), written)
            _write(args.summary_dir / "runs.jsonl",
                   "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in run_rows(raw)), written)
        for path in written:
            print(f"wrote {path}", file=sys.stderr)
    print(json.dumps(jsonable(result.get("headline", {})), ensure_ascii=False, indent=1, sort_keys=True))
    for host in HOSTS:
        res = result["hosts"][host]
        print(f"host {host}")
        for name, *_ in SR_CONTRASTS:
            c = res["contrasts"][name]
            print(f"  {name}: {_ci(c)}")
        for label in ARM_ORDER:
            a = res["arms"][label]
            print(f"  {label} {ARMS[label][0]}: plateau_SR {_f(a['plateau_sr'])} harmful_absorbed "
                  f"{a['absorption']['harmful']['absorbed']['total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
