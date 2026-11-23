"""WP0: the MCP bridge needs ``mcp>=2``; a missing or 1.x SDK must give a
module-level skip with a reason, never a pytest collection error.

These tests do not need the SDK: they fake an ``mcp`` distribution on
``sys.path`` (a package plus ``dist-info`` metadata) in a subprocess.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_a2a  # noqa: E402

PYPROJECT = (REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_pyproject_pins_the_same_requirement():
    assert f"'{mcp_a2a.MCP_REQUIREMENT}; python_version >= \"3.10\"'" in PYPROJECT
    assert mcp_a2a.MCP_REQUIREMENT.startswith("mcp>=2")


@pytest.mark.parametrize(
    "installed, ok",
    [("1.30.0", False), ("1.99", False), ("2.0.0", True), ("2.3.0", True), ("3.0.0", False), ("dev", False)],
)
def test_reason_by_installed_version(monkeypatch, installed, ok):
    import importlib.metadata as md

    monkeypatch.setattr(md, "version", lambda name: installed)
    reason = mcp_a2a.mcp_unavailable_reason()
    if sys.version_info < (3, 10):
        assert reason and "3.10" in reason
    elif ok:
        assert reason is None
    else:
        assert reason and installed in reason and mcp_a2a.MCP_REQUIREMENT in reason


def test_reason_when_not_installed(monkeypatch):
    import importlib.metadata as md

    def missing(name):
        raise md.PackageNotFoundError(name)

    monkeypatch.setattr(md, "version", missing)
    reason = mcp_a2a.mcp_unavailable_reason()
    assert reason and ("not installed" in reason or "3.10" in reason)


def _fake_mcp(root: Path, version: str) -> Path:
    pkg = root / "mcp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("# fake mcp 1.x: no Client, no mcp.server.MCPServer\n")
    meta = root / f"mcp-{version}.dist-info"
    meta.mkdir()
    (meta / "METADATA").write_text(f"Metadata-Version: 2.1\nName: mcp\nVersion: {version}\n")
    return root


def test_bridge_tests_skip_with_reason_under_mcp_1x(tmp_path):
    site = _fake_mcp(tmp_path / "site", "1.30.0")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(site), str(REPO)]))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", str(REPO / "tests" / "mcp_bridge" / "test_bridge.py")],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    # 2 = interrupted by a collection error; 0 or 5 (no test ran) = clean module skip.
    assert proc.returncode in (0, 5), out
    assert "error" not in out.lower()
    assert "1 skipped" in out
    expected = "needs Python >= 3.10" if sys.version_info < (3, 10) else "mcp 1.30.0 is installed but the bridge needs mcp>=2,<3"
    assert expected in out


def test_server_import_fails_clearly_under_mcp_1x(tmp_path):
    site = _fake_mcp(tmp_path / "site", "1.30.0")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(site), str(REPO)]))
    proc = subprocess.run(
        [sys.executable, "-c", "import mcp_a2a.server"], cwd=REPO, env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "ImportError: mcp_a2a.server:" in proc.stderr
