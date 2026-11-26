"""Tests for the expiry runner (``experiments/expiry/run.py``), part 1g.

Fast: every invocation uses two 200-task runs at most. The analysis rules are
tested in ``test_expiry_analysis.py``; here the CLI contract and the generated
report wording of §14 are checked.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from experiments.expiry import run as runner
from experiments.expiry.analysis import RULE_LABELS
from experiments.expiry.sim import ARM_ORDER, PILOT_RUNS, PILOT_SEED, PILOT_TASKS

REPO_ROOT = Path(__file__).resolve().parents[2]
TINY = ["--seeds", "42", "--runs", "2", "--run-start", "100", "--tasks", "200"]
ONE = ["--seeds", "42", "--runs", "1", "--run-start", "100", "--tasks", "200"]
FORBIDDEN = ["no effect", "no measurable increment", "no increment", "expiry is useless",
             "harmless", "accountable"]
CHINESE = re.compile(r"[\u4e00-\u9fff]")


def _repo_files() -> set:
    """Every file under the repo's expiry experiment dir, plus ``git status``."""
    paths = {str(p) for p in (REPO_ROOT / "experiments" / "expiry").rglob("*")
             if "__pycache__" not in p.parts}
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True,
                                capture_output=True, check=False)
        if status.returncode == 0:
            paths |= {"git-status:" + line for line in status.stdout.splitlines()}
    except OSError:  # pragma: no cover - no git binary
        pass
    return paths


def _tiny_report() -> str:
    """The report of a two-run invocation, built in process."""
    raw = runner.run_all([42], [100, 101], 200)
    result = runner.analyse(raw)
    return runner.results_markdown(result, [42], [100, 101], 200, "test-commit")


def test_cli_default_writes_nothing(capsys: pytest.CaptureFixture) -> None:
    before = _repo_files()
    assert runner.main(ONE) == 0
    out = capsys.readouterr().out
    assert "Verdict:" in out
    assert _repo_files() == before


def test_results_flag_writes_only_the_given_path(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "REPORT.md"
    assert runner.main(ONE + ["--results", str(target)]) == 0
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert runner.GENERATED_NOTE in text
    assert not (tmp_path / "nested" / "runs.jsonl").exists()


def test_summary_dir_writes_runs_jsonl_and_summary_json(tmp_path: Path) -> None:
    assert runner.main(TINY + ["--summary-dir", str(tmp_path), "--commit", "deadbeef"]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["prereg_merge_commit"] == runner.PREREG_MERGE_COMMIT
    assert summary["prereg_file_commit"] == runner.PREREG_FILE_COMMIT
    assert summary["runner_base_commit"] == "deadbeef"
    assert summary["seeds"] == [42] and summary["runs"] == [100, 101] and summary["tasks"] == 200
    assert summary["amendments"] == "none"
    assert summary["result"]["primary"]["contrast"] == "D-C:recovery_window_SR"
    rows = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["seed"], r["run"]) for r in rows] == [(42, 100), (42, 101)]
    for row in rows:
        assert list(row["arms"]) == list(ARM_ORDER)
        assert "recovery_window_sr" in row["arms"]["D"]


def test_results_and_summary_dir_bare_flags_use_committed_paths() -> None:
    assert runner.DEFAULT_RESULTS == Path(runner.HERE) / "EXPIRY_RESULTS.md"
    assert runner.DEFAULT_SUMMARY_DIR == Path(runner.HERE) / "summaries"
    args = runner.build_parser().parse_args(["--results", "--summary-dir"])
    assert args.results == runner.DEFAULT_RESULTS
    assert args.summary_dir == runner.DEFAULT_SUMMARY_DIR
    plain = runner.build_parser().parse_args([])
    assert plain.results is None and plain.summary_dir is None and plain.pilot is False


def test_jobs_does_not_change_stdout() -> None:
    def run(jobs: str) -> bytes:
        proc = subprocess.run(["python3", "-m", "experiments.expiry.run", *TINY, "--jobs", jobs],
                              cwd=REPO_ROOT, capture_output=True, check=True)
        return proc.stdout

    assert run("1") == run("2")


def test_report_has_required_content_and_caveats() -> None:
    report = _tiny_report()
    assert "Verdict:" in report
    labels = set(RULE_LABELS.values())
    found = [label for label in labels if f"`{label}`" in report]
    assert found, "the report must state the verdict label verbatim from the §12 label set"
    for required in (runner.GENERATED_NOTE, "Amendments: none", runner.CAVEAT_CONVENTION,
                     runner.CAVEAT_SCENARIO, runner.CAVEAT_BOUNDED, runner.CAVEAT_HALF_WIDTH,
                     "non-normative single-project convention", "not an LLM receiver",
                     "rule-parser", "one honest peer", "v2 lease rule",
                     "share of detected shifts", "D − C `recovery_window_SR`",
                     "`pre_shift_SR`", "`full_sequence_SR`", "Half-width flag (> 0.034)",
                     "V1", "V2", "Saturation", "25%", "50%",
                     "python -m experiments.expiry.run", "Per-arm descriptive table",
                     "tasks_to_recover"):
        assert required in report, required
    for header in ("## Headline", "## Per-arm descriptive table", "## Secondary contrasts",
                   "## Per-seed means", "## Strata", "## Saturation check", "## Reproduce"):
        assert header in report, header


def test_report_avoids_forbidden_phrases() -> None:
    report = _tiny_report()
    lowered = report.lower()
    for phrase in FORBIDDEN:
        assert phrase not in lowered, phrase


def test_report_has_no_chinese_characters() -> None:
    assert not CHINESE.search(_tiny_report())


def test_pilot_argument_resolution_and_report() -> None:
    args = runner.build_parser().parse_args(["--pilot"])
    seeds, runs, n_tasks = runner.resolve_args(args)
    assert seeds == [PILOT_SEED] == [42]
    assert runs == list(PILOT_RUNS) == [100, 101]
    assert n_tasks == PILOT_TASKS == 200
    # --pilot wins over the other sample flags (§13).
    seeds, runs, n_tasks = runner.resolve_args(
        runner.build_parser().parse_args(["--pilot", "--seeds", "7", "--runs", "5", "--tasks", "3"]))
    assert (seeds, runs, n_tasks) == ([42], [100, 101], 200)

    raw = runner.run_all(seeds, runs, n_tasks)
    report = runner.results_markdown(runner.analyse(raw), seeds, runs, n_tasks, "test-commit", pilot=True)
    assert "Pilot only" in report
    assert "not part of the analysis" in report
    non_pilot = runner.results_markdown(runner.analyse(raw), seeds, runs, n_tasks, "test-commit")
    assert "Pilot only" not in non_pilot

