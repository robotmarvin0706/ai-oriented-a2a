"""WP0: ``run.py`` writes nothing unless asked. A plain run prints to stdout
and leaves the committed RESULTS.md, summaries and datasets byte-identical."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.timeout_recovery import run as runner  # noqa: E402

COMMITTED = [
    REPO / "experiments" / "timeout_recovery" / "RESULTS.md",
    REPO / "experiments" / "timeout_recovery" / "summaries",
    REPO / "datasets" / "timeout_recovery" / "runs",
]


def _snapshot():
    out = {}
    for root in COMMITTED:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for p in files:
            out[str(p.relative_to(REPO))] = p.read_bytes()
    return out


def test_plain_run_writes_nothing_and_prints_tables():
    before = _snapshot()
    proc = subprocess.run(
        [sys.executable, "-m", "experiments.timeout_recovery.run", "--seeds", "42", "--n", "3"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    assert "| distilled_packet |" in proc.stdout and "seed 42" in proc.stdout
    assert proc.stdout.startswith("strategy model: deterministic-rule-host-v1\n")
    assert "wrote " not in proc.stderr
    assert _snapshot() == before


def test_write_outputs_is_not_called_without_flags(monkeypatch):
    def boom(*a, **k):  # pragma: no cover - fails the test if reached
        raise AssertionError("write_outputs called without --results/--summary-dir/--data-dir")

    monkeypatch.setattr(runner, "write_outputs", boom)
    assert runner.main(["--seeds", "7", "--n", "2"]) == 0


def test_bare_flags_select_the_committed_paths(monkeypatch):
    seen = {}

    def capture(result, seeds, n, model_name, data_dir, results_path, commit, full_seeds=None, summary_dir=None):
        seen.update(data_dir=data_dir, results=results_path, summary=summary_dir)
        return {}

    monkeypatch.setattr(runner, "write_outputs", capture)
    runner.main(["--seeds", "7", "--n", "2", "--results", "--commit", "x"])
    assert seen == {"data_dir": None, "results": runner.DEFAULT_RESULTS, "summary": None}
    seen.clear()
    runner.main(["--seeds", "7", "--n", "2", "--results", "--summary-dir", "--data-dir", "--commit", "x"])
    assert seen == {"data_dir": runner.DEFAULT_DATA_DIR, "results": runner.DEFAULT_RESULTS, "summary": runner.DEFAULT_SUMMARY_DIR}


def test_explicit_results_path_writes_only_there(tmp_path):
    before = _snapshot()
    target = tmp_path / "R.md"
    runner.main(["--seeds", "7", "--n", "2", "--results", str(target), "--commit", "x"])
    assert target.read_text(encoding="utf-8").startswith("# Timeout-recovery")
    assert _snapshot() == before
