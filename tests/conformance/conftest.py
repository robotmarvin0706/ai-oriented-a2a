"""Implementation selection for the HTTP conformance suite.

``start_receiver(sign_key=None, replay_window=None)`` returns the base URL
of a receiver configured with that signing key and replay window. It is
parametrized over the implementations named in ``A2A_CONFORMANCE_IMPLS``
(comma-separated). Default: ``reference,second_impl``, plus ``external``
when ``A2A_PEER_CMD`` or ``A2A_PEER_URL`` is set.

- ``reference``: examples/network_dual_end.py, in-process.
- ``second_impl``: second_impl/minimal_client.py receiver, in-process.
- ``external``: an implementation under test, either
  - ``A2A_PEER_CMD``: a command template containing ``{port}``. The runner
    starts a fresh process per configuration, appending
    ``--sign-key KEY`` and/or ``--replay-window SECONDS`` when a test needs
    them, and waits for the port to accept connections; or
  - ``A2A_PEER_URL``: an already running receiver whose configuration is
    described by ``A2A_PEER_SIGN_KEY`` and ``A2A_PEER_REPLAY_WINDOW``.
    Tests that need a different configuration are skipped. A long-running
    peer keeps its replay state across tests, so ``A2A_PEER_CMD`` is the
    more faithful mode.

See docs/COMPLIANCE.md.
"""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from http.server import HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
for _p in (str(REPO), str(EXAMPLES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _impls():
    raw = os.environ.get("A2A_CONFORMANCE_IMPLS")
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    impls = ["reference", "second_impl"]
    if os.environ.get("A2A_PEER_CMD") or os.environ.get("A2A_PEER_URL"):
        impls.append("external")
    return impls


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port, proc, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"external peer exited with code {proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"external peer did not listen on port {port} within {timeout}s")


def _same_window(a, b):
    if a is None or b is None:
        return a is None and b is None
    return float(a) == float(b)


@pytest.fixture(params=_impls())
def start_receiver(request):
    impl = request.param
    cleanups = []

    def _start(sign_key=None, replay_window=None):
        if impl == "reference":
            from minimal_dual_end import Receiver
            from network_dual_end import A2AHandler

            A2AHandler.role = "receiver"
            A2AHandler.peer_url = None
            A2AHandler.sign_key = sign_key
            A2AHandler.replay_window = replay_window
            A2AHandler.received = []
            A2AHandler.receiver = Receiver(isolation_n=3, regression_drop_threshold=0.10)
            A2AHandler.seen_nonces = set()
            server = HTTPServer(("127.0.0.1", 0), A2AHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cleanups.append(lambda: (server.shutdown(), server.server_close()))
            return f"http://127.0.0.1:{server.server_address[1]}"

        if impl == "second_impl":
            from second_impl.minimal_client import start_in_thread

            server, url = start_in_thread(sign_key=sign_key, replay_window=replay_window)
            cleanups.append(lambda: (server.shutdown(), server.server_close()))
            return url

        if impl == "external":
            template = os.environ.get("A2A_PEER_CMD")
            if template:
                port = _free_port()
                cmd = shlex.split(template.format(port=port))
                if sign_key:
                    cmd += ["--sign-key", sign_key]
                if replay_window is not None:
                    cmd += ["--replay-window", str(replay_window)]
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                cleanups.append(lambda: (proc.terminate(), proc.wait(timeout=10)))
                _wait_for_port(port, proc)
                return f"http://127.0.0.1:{port}"
            url = os.environ.get("A2A_PEER_URL")
            peer_key = os.environ.get("A2A_PEER_SIGN_KEY") or None
            peer_window = os.environ.get("A2A_PEER_REPLAY_WINDOW") or None
            if peer_key != sign_key or not _same_window(peer_window, replay_window):
                pytest.skip(
                    f"external peer is configured with sign_key={peer_key!r}, "
                    f"replay_window={peer_window!r}; test needs {sign_key!r}, {replay_window!r} "
                    "(use A2A_PEER_CMD to run every configuration)"
                )
            return url

        raise ValueError(f"unknown implementation {impl!r} in A2A_CONFORMANCE_IMPLS")

    yield _start
    for cleanup in reversed(cleanups):
        cleanup()
