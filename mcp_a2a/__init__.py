"""MCP bridge to AI-oriented A2A v0.4 peers (outbound messages and a local lookup). See README.md.

Importing the package does not import the MCP SDK; ``mcp_a2a.server`` does.
``mcp_unavailable_reason()`` tells callers (and the tests) whether the
installed SDK satisfies ``MCP_REQUIREMENT`` without importing it.

Outbound signing is mandatory: ``require_sign_key()`` raises ``MissingSignKey``
when ``A2A_SIGN_KEY`` is unset or blank, so the bridge never sends an unsigned
message. The key is read from the environment only, never from a tool argument.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Dict, Optional

#: Environment variable holding the HMAC-SHA256 key for outbound messages.
SIGN_KEY_ENV = "A2A_SIGN_KEY"


class MissingSignKey(ValueError):
    """``A2A_SIGN_KEY`` is unset or blank; the bridge refuses to send unsigned."""

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


def require_sign_key(environ: Optional[Dict[str, str]] = None) -> str:
    """The HMAC-SHA256 key for outbound messages, or ``MissingSignKey``.

    There is no unsigned fallback: a bridge that silently drops the signature
    would let a misconfigured host send unauthenticated Experience Packets,
    which is exactly what SPEC §2.1 and docs/SECURITY.md warn about.
    """
    env = os.environ if environ is None else environ
    key = (env.get(SIGN_KEY_ENV) or "").strip()
    if not key:
        raise MissingSignKey(
            f"{SIGN_KEY_ENV} is not set: the MCP bridge signs every outbound message and "
            "refuses to send unsigned ones"
        )
    return key


__all__ = ["MCP_REQUIREMENT", "MissingSignKey", "SIGN_KEY_ENV", "mcp_unavailable_reason", "require_sign_key"]
