"""One-way MCP bridge to AI-oriented A2A v0.4 peers. See README.md.

Importing the package does not import the MCP SDK; ``mcp_a2a.server`` does.
``mcp_unavailable_reason()`` tells callers (and the tests) whether the
installed SDK satisfies ``MCP_REQUIREMENT`` without importing it.
"""

from __future__ import annotations

import re
import sys
from typing import Optional

# Same bound as the ``mcp`` extra in pyproject.toml.
MCP_REQUIREMENT = "mcp>=2,<3"
MCP_MIN_MAJOR = 2
MCP_MAX_MAJOR_EXCLUSIVE = 3


def mcp_unavailable_reason() -> Optional[str]:
    """None when an ``mcp`` SDK in ``MCP_REQUIREMENT`` is installed, else why not."""
    if sys.version_info < (3, 10):
        return f"the MCP bridge needs Python >= 3.10 (running {sys.version_info[0]}.{sys.version_info[1]})"
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - Python < 3.8
        return "importlib.metadata unavailable"
    try:
        installed = version("mcp")
    except PackageNotFoundError:
        return f"mcp is not installed (needs {MCP_REQUIREMENT})"
    m = re.match(r"\s*(\d+)", installed)
    if m is None:
        return f"mcp {installed} has an unreadable version (needs {MCP_REQUIREMENT})"
    major = int(m.group(1))
    if not MCP_MIN_MAJOR <= major < MCP_MAX_MAJOR_EXCLUSIVE:
        return f"mcp {installed} is installed but the bridge needs {MCP_REQUIREMENT}"
    return None


__all__ = ["MCP_REQUIREMENT", "mcp_unavailable_reason"]
