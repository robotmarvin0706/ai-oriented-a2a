"""Tests for the budget-experiment runner (`experiments/budget/run.py`).

Fast by design: every CLI invocation is two runs of 100 tasks. The suite checks the
WP0 output convention (nothing written without a flag), the `--jobs` determinism
promise, the §12/§14 contract of the generated report, and the §13 pilot resolution.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from experiments.budget import run as run_mod
from experiments.budget.analysis import (
    LABEL_R1A,
    LABEL_R1B,
    LABEL_R2,
    LABEL_R3_PREFIX,
    LABEL_R4,
    LABEL_VOID,
)
from experiments.budget.run import build_parser, resolve_args
from experiments.budget.sim import ARM_ORDER

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = "experiments.budget.run"
TINY = ["--seeds", "42", "--runs", "2", "--run-start", "100", "--commit", "testcommit"]

LABELS = (LABEL_VOID, LABEL_R1A, LABEL_R1B, LABEL_R3_PREFIX, LABEL_R2, LABEL_R4)

FORBIDDEN = [
    "cheaper",
    "saves tokens",
    "skips verification",
    "no harm",
    "no effect",
    "repro is useless",
    "no increment",
    "accountable",
]
FORBIDDEN_RE = re.compile(r"\bsafe\b", re.IGNORECASE)
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def cli(args, cwd=REPO_ROOT):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + env["PYTHONPATH"]
                                          if env.get("PYTHONPATH") else "")
    return subprocess.run([sys.executable, "-m", MODULE, *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=300)


def repo_files():
    return {str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "experiments" / "budget").rglob("*")
            if "__pycache__" not in p.parts and p.is_file()}


@pytest.fixture(scope="module")
def tiny_report():
    proc = cli(TINY)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_default_writes_nothing(tmp_path):
    before = repo_files()
    proc = cli(TINY, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()
    assert repo_files() == before
    assert list(tmp_path.iterdir()) == []
    args = build_parser().parse_args([])
    assert args.results is None and args.summary_dir is None
    assert resolve_args(args) == ([42, 7, 2026, 31337], list(range(40)), 100)


def test_results_flag_writes_given_path(tmp_path, tiny_report):
    target = tmp_path / "nested" / "report.md"
    proc = cli(TINY + ["--results", str(target)])
    assert proc.returncode == 0, proc.stderr
    assert target.read_text(encoding="utf-8") == tiny_report
    assert "do not hand-edit" in tiny_report


def test_summary_dir_writes_runs_and_summary(tmp_path):
    out = tmp_path / "summaries"
    proc = cli(TINY + ["--summary-dir", str(out)])
    assert proc.returncode == 0, proc.stderr
    rows = (out / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    for line in rows:
        assert line == json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"))
    assert [(r["seed"], r["run"]) for r in map(json.loads, rows)] == [(42, 100), (42, 101)]
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["amendments"] == "none"
    assert summary["prereg_merge_commit"] == run_mod.PREREG_MERGE_COMMIT
    assert summary["result"]["n_runs"] == 2


def test_jobs_output_is_byte_identical(tiny_report):
    two = cli(TINY + ["--jobs", "2"])
    assert two.returncode == 0, two.stderr
    assert two.stdout == tiny_report


def test_headline_carries_token_ci_delta_sr_and_harm(tiny_report):
    head = tiny_report.split("## Headline", 1)[1].split("## Validity checks", 1)[0]
    assert "**Verdict: `" in head
    label = head.split("**Verdict: `", 1)[1].split("`", 1)[0]
    assert label.startswith(LABELS) or label in LABELS
    assert "rel_token_reduction(D2 vs C)" in head
    assert re.search(r"\[[+-]\d\.\d{3}, [+-]\d\.\d{3}\]", head)
    assert "Δ plateau_SR (D2 − C)" in head
    assert "harmful_absorption_rate(D2) − harmful_absorption_rate(C)" in head
    assert "tasks_on_harmful_policy" in head
    assert "persistent harm" in head.lower()


def test_report_contains_required_caveats(tiny_report):
    for phrase in (
        "single-project, non-normative receiver rule over the v0.4 `verification_claim` fields",
        "`impls/go/` reads `verification_claim.repro.n` only for the experiment 1 `claim_selection`",
        "does not use `verification_claim.repro` for gate skipping",
        "one simulated tool (`web_search` timeout recovery)",
        "rule-parser receiver",
        "not an LLM receiver and not a real API",
        "74-75 tokens",
        "Amendments: none",
        "python3 -m experiments.budget.run --jobs 4 --results --summary-dir",
        "reported, no verdict (§8.3)",
    ):
        assert phrase in tiny_report, phrase


def test_report_records_pilot_findings(tiny_report):
    assert "## Pilot findings (§13 step 3)" in tiny_report
    assert "**Bugs fixed after pilot: none.**" in tiny_report


def test_report_only_section_has_rule_na(tiny_report):
    """§8.3 endpoints carry rule `n/a` per row and no verdict wording."""
    lines = tiny_report.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("## Report-only secondary endpoints"))
    end = next(i for i, line in enumerate(lines[start + 1:], start + 1)
               if line.startswith("## "))
    section = lines[start:end]
    rows = [line for line in section if line.startswith("| ") and not line.startswith("| --- ")]
    header = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    assert rows[0].startswith("| Arm |") and "Rule" in header
    rule_ix = header.index("Rule")
    assert len(rows) == 1 + len(ARM_ORDER)
    for row in rows[1:]:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        assert cells[rule_ix] == "n/a", row
    body = "\n".join(section).lower()
    assert "supported" not in body
    assert "worse" not in body


def test_report_wording_is_clean(tiny_report):
    low = tiny_report.lower()
    for phrase in FORBIDDEN:
        assert phrase not in low, phrase
    assert not FORBIDDEN_RE.search(tiny_report)
    assert not CHINESE_RE.search(tiny_report)
    if LABEL_R1A not in tiny_report:
        assert "supported" not in low
    # An R1b row is never restated as support.
    for line in tiny_report.splitlines():
        if LABEL_R1B in line and LABEL_R1A not in line:
            assert "supported" not in line.lower()


def test_pilot_resolution_and_report():
    args = build_parser().parse_args(["--pilot", "--seeds", "7", "--runs", "5", "--tasks", "10"])
    assert resolve_args(args) == ([42], [100, 101], 100)
    proc = cli(["--pilot", "--commit", "testcommit"])
    assert proc.returncode == 0, proc.stderr
    assert "PILOT" in proc.stdout
    assert "not part of the analysis" in proc.stdout
    assert "run indices 100-101" in proc.stdout
