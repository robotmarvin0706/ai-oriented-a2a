"""Negotiation guards for protocol v0.4 (SPEC §3): core constraints, round cap,
per-peer rate limit. Standard library only.

``immutable_constraint_rejection(msg, core_constraints)``
    A propose or counter whose ``target_constraint`` is one of the receiver's
    ``core_constraints`` MUST be rejected with a reason starting with
    ``immutable_constraint:``. Matching is exact string equality. Core
    constraints are changed only by the host, off-protocol; nothing in a
    protocol message changes them.

``NegotiationGuard``
    Thread-safe state for one receiver:

    - Round cap: every negotiation message that carries a ``negotiation_id``
      counts as one round for that id, whatever the reply. When the count
      exceeds ``max_rounds`` (default ``DEFAULT_MAX_NEGOTIATION_ROUNDS`` = 3,
      overridable per guard) the message is rejected with
      ``negotiation_limit: ...``. A message without ``negotiation_id`` is
      never counted and is never rejected for lacking one (SPEC: a receiver
      MAY treat it as a single round); different ``proposal_id`` values are
      never added up.
    - Per-peer rate limit (SHOULD in SPEC, numbers implementation-defined):
      off by default. With ``peer_rate_limit=N`` a peer key (for example the
      client address; the host chooses) may send at most N negotiation
      messages per ``peer_rate_window`` seconds; more are rejected with
      ``negotiation_limit: peer rate limit exceeded``.
    - Memory is bounded: at most ``MAX_TRACKED_NEGOTIATIONS`` ids are kept;
      the oldest are dropped first.

``NegotiationGuard.check(msg, core_constraints=(), peer=None)`` returns a
rejected Absorption Result or None, in this order (after the caller's
``content_policy:`` check, which always comes first, SPEC §2.1):

1. ``negotiation_limit:`` (round cap, then peer rate limit)
2. ``immutable_constraint:``

A None result means the host decides as usual (for example
``negotiation_unsupported:``). ``second_impl/`` has its own equivalent and
does not import this package.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, Iterable, Optional

from .protocol import packet_ref_of

IMMUTABLE_CONSTRAINT_PREFIX = "immutable_constraint:"
NEGOTIATION_LIMIT_PREFIX = "negotiation_limit:"
DEFAULT_MAX_NEGOTIATION_ROUNDS = 3
MAX_TRACKED_NEGOTIATIONS = 10_000
DEFAULT_PEER_RATE_WINDOW = 60.0


def _nested_ref(msg: Dict[str, Any]) -> str:
    offered = msg.get("offered_packet")
    if isinstance(offered, dict):
        return packet_ref_of(offered)
    return packet_ref_of(None)


def _reject(msg: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {"packet_ref": _nested_ref(msg), "status": "rejected", "reason": reason}


def is_propose_or_counter(msg: Dict[str, Any]) -> bool:
    if "proposal_id" not in msg:
        return False
    return msg.get("status") == "counter" or ("status" not in msg and "proposed_change" in msg)


def immutable_constraint_rejection(
    msg: Dict[str, Any], core_constraints: Iterable[str]
) -> Optional[Dict[str, Any]]:
    """Rejected result if a propose/counter targets one of ``core_constraints``."""
    if not isinstance(msg, dict) or not is_propose_or_counter(msg):
        return None
    target = msg.get("target_constraint")
    if not isinstance(target, str):
        return None
    if target in set(c for c in core_constraints if isinstance(c, str)):
        return _reject(
            msg,
            f"{IMMUTABLE_CONSTRAINT_PREFIX} {target} is a core constraint and cannot be negotiated",
        )
    return None


class NegotiationGuard:
    """Round cap per ``negotiation_id`` and optional per-peer rate limit."""

    def __init__(
        self,
        max_rounds: int = DEFAULT_MAX_NEGOTIATION_ROUNDS,
        *,
        peer_rate_limit: Optional[int] = None,
        peer_rate_window: float = DEFAULT_PEER_RATE_WINDOW,
        max_tracked: int = MAX_TRACKED_NEGOTIATIONS,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        if peer_rate_limit is not None and peer_rate_limit < 1:
            raise ValueError("peer_rate_limit must be at least 1 (or None for off)")
        self.max_rounds = int(max_rounds)
        self.peer_rate_limit = peer_rate_limit
        self.peer_rate_window = float(peer_rate_window)
        self.max_tracked = int(max_tracked)
        self._rounds: "OrderedDict[str, int]" = OrderedDict()
        self._peer_times: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def rounds(self, negotiation_id: str) -> int:
        with self._lock:
            return self._rounds.get(negotiation_id, 0)

    def _count_round(self, negotiation_id: str) -> int:
        count = self._rounds.pop(negotiation_id, 0) + 1
        self._rounds[negotiation_id] = count
        while len(self._rounds) > self.max_tracked:
            self._rounds.popitem(last=False)
        return count

    def _peer_over_limit(self, peer: str, now: float) -> bool:
        times = self._peer_times.setdefault(peer, deque())
        while times and now - times[0] >= self.peer_rate_window:
            times.popleft()
        times.append(now)
        if len(self._peer_times) > self.max_tracked:
            for key in [k for k, v in self._peer_times.items() if not v or now - v[-1] >= self.peer_rate_window]:
                del self._peer_times[key]
        return len(times) > (self.peer_rate_limit or 0)

    def check(
        self,
        msg: Dict[str, Any],
        core_constraints: Iterable[str] = (),
        peer: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record this negotiation message and return a rejection or None."""
        if not isinstance(msg, dict) or "proposal_id" not in msg:
            return None
        now = time.time() if now is None else now
        nid = msg.get("negotiation_id")
        over_rounds = False
        over_rate = False
        with self._lock:
            if isinstance(nid, str) and nid:
                over_rounds = self._count_round(nid) > self.max_rounds
            if self.peer_rate_limit is not None and peer is not None:
                over_rate = self._peer_over_limit(peer, now)
        if over_rounds:
            return _reject(
                msg,
                f"{NEGOTIATION_LIMIT_PREFIX} negotiation_id {nid} exceeded {self.max_rounds} rounds",
            )
        if over_rate:
            return _reject(msg, f"{NEGOTIATION_LIMIT_PREFIX} peer rate limit exceeded")
        return immutable_constraint_rejection(msg, core_constraints)


__all__ = [
    "DEFAULT_MAX_NEGOTIATION_ROUNDS",
    "DEFAULT_PEER_RATE_WINDOW",
    "IMMUTABLE_CONSTRAINT_PREFIX",
    "MAX_TRACKED_NEGOTIATIONS",
    "NEGOTIATION_LIMIT_PREFIX",
    "NegotiationGuard",
    "immutable_constraint_rejection",
    "is_propose_or_counter",
]
