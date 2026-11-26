"""Peers, packet pool and envelope fields (PREREG_SELECTION §6).

Everything here is derived from ``(seed, run)`` through ``stable_int`` seed
parts, so a pool is byte-identical across processes. Packet ``content`` uses
the ``recovery_policy`` format of ``experiments/timeout_recovery/packets.py``.
The envelope fields (``prior_outcomes``, ``provenance``, ``verification_claim``)
are what the selection rules of §7 may read; ``content`` is what the gate
parses. Ground truth (family, peer) is kept beside the packet, never inside it.
"""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ai_oriented_a2a import content_policy_rejection
from ai_oriented_a2a.recovery import parse_recovery_params, policy_from_params

from experiments.timeout_recovery.host import (
    MODEL_NAME,
    Case,
    Host,
    naive_policy,
    run_window,
    stable_int,
)
from experiments.timeout_recovery.packets import SCENARIO

# ---------------------------------------------------------------------------
# Constants fixed by the pre-registration
# ---------------------------------------------------------------------------

POOL_SIZE = 50
PACKETS_PER_PEER = 10
#: (peer id, effective packets, mediocre packets, harmful packets, honest) - §6.1
PEERS: Tuple[Tuple[str, int, int, int, bool], ...] = (
    ("peer-eff-1", 2, 8, 0, True),
    ("peer-eff-2", 1, 9, 0, True),
    ("peer-med-1", 0, 10, 0, True),
    ("peer-med-2", 0, 10, 0, True),
    ("peer-dis-1", 0, 0, 10, False),
)
DISHONEST_PEER = "peer-dis-1"

EFFECTIVE_BASE_BACKOFF_MS = (400, 800)
EFFECTIVE_MAX_ATTEMPTS = (4, 5)
MEDIOCRE_BASE_BACKOFF_MS = (50, 100)
MEDIOCRE_MAX_ATTEMPTS = (2, 3)
HARMFUL_MAX_ATTEMPTS = (6, "unbounded")

PRIOR_M_RANGE = (3, 12)  # §6.2 step 1, inclusive
PRIOR_FLIP_P = 0.15  # §6.2 step 3, phi
CLAIM_N = 30  # §6.2 honest verification_claim sample size
CLAIM_CASE_OFFSET = 1000  # claim cases use j >= this, prior receivers use j < 12
DISHONEST_ABSORB_P = 0.9  # §6.3
DISHONEST_CLAIM_RANGE = (0.85, 0.95)  # §6.3
CLAIM_METRIC = "recovery_sr"
EXPECTED_EFFECT = "recover from web_search timeouts without throttling or accepting late partial results"

FAMILIES = ("effective", "mediocre", "harmful")


@dataclass
class PoolPacket:
    """One packet of a run's pool plus its ground truth (never sent)."""

    packet: Dict[str, Any]  # the full packet as sent over the protocol path
    peer: str  # true author
    family: str  # effective / mediocre / harmful

    @property
    def id(self) -> str:
        return self.packet["id"]


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def _content(family: str, rng: random.Random) -> Dict[str, Any]:
    if family == "effective":
        ms = rng.choice(EFFECTIVE_BASE_BACKOFF_MS)
        n = rng.choice(EFFECTIVE_MAX_ATTEMPTS)
        return {
            "trigger": SCENARIO,
            "recovery_policy": {"backoff": "exponential", "base_backoff_ms": ms, "max_attempts": n,
                                "drop_stale_partials": True},
            "compressed_signal": f"exponential backoff from {ms} ms, at most {n} attempts, drop late partial results",
        }
    if family == "mediocre":
        ms = rng.choice(MEDIOCRE_BASE_BACKOFF_MS)
        n = rng.choice(MEDIOCRE_MAX_ATTEMPTS)
        return {
            "trigger": SCENARIO,
            "recovery_policy": {"backoff": "exponential", "base_backoff_ms": ms, "max_attempts": n,
                                "drop_stale_partials": False},
            "compressed_signal": f"exponential backoff from {ms} ms, at most {n} attempts",
        }
    if family == "harmful":
        n = rng.choice(HARMFUL_MAX_ATTEMPTS)
        text = "up to 6 attempts" if n == 6 else "keep retrying until a result arrives"
        return {
            "trigger": SCENARIO,
            "recovery_policy": {"backoff": "none", "max_attempts": n, "drop_stale_partials": False},
            "compressed_signal": f"retry immediately without backoff, {text}; keep partial results",
        }
    raise ValueError(f"unknown family {family!r}")


def packet_id(seed: int, run: int, peer: str, k: int) -> str:
    """Opaque id: does not reveal the peer or the family."""
    return f"pkt-sel-{stable_int('pid', seed, run, peer, k):016x}"


def base_packets(seed: int, run: int) -> List[PoolPacket]:
    """The 50 packets with content only (no envelope), in peer order (§6.1)."""
    rng = random.Random(stable_int("pool", seed, run))
    out: List[PoolPacket] = []
    for peer, n_eff, n_med, n_harm, _honest in PEERS:
        families = ["effective"] * n_eff + ["mediocre"] * n_med + ["harmful"] * n_harm
        rng.shuffle(families)  # which packets of an eff peer are effective is drawn per run
        for k, family in enumerate(families):
            packet = {
                "id": packet_id(seed, run, peer, k),
                "version": "0.4",
                "type": "failure_mode",
                "content": _content(family, rng),
                "absorption": {"method": "prompt_injection", "target": "tool_retry_policy"},
            }
            out.append(PoolPacket(packet, peer, family))
    assert len(out) == POOL_SIZE
    return out


# ---------------------------------------------------------------------------
# Envelope fields (§6.2, §6.3)
# ---------------------------------------------------------------------------


def prior_case(seed: int, run: int, pid: str, j: int) -> Case:
    """A fresh naive_host case from the independent ``prior`` stream (§6.2 step 2)."""
    return Case(stable_int("prior", seed, run, pid, j), 0)


def prior_receiver_status(packet: Dict[str, Any], case: Case) -> str:
    """What one earlier naive_host receiver did with the packet: the same gate."""
    host = Host(case)
    host.policy = naive_policy()
    return host.handle_message(deepcopy(packet))["status"]


def honest_prior_outcomes(seed: int, run: int, packet: Dict[str, Any]) -> Dict[str, Any]:
    """§6.2: m earlier receivers, each outcome flipped with probability phi."""
    pid = packet["id"]
    rng = random.Random(stable_int("prior-noise", seed, run, pid))
    m = rng.randint(*PRIOR_M_RANGE)
    outcomes: List[str] = []
    for j in range(m):
        absorbed = prior_receiver_status(packet, prior_case(seed, run, pid, j)) == "absorbed"
        if rng.random() < PRIOR_FLIP_P:
            absorbed = not absorbed
        outcomes.append("absorbed" if absorbed else "rejected")
    a = sum(1 for o in outcomes if o == "absorbed")
    return {"absorbed_count": a, "rejected_count": m - a, "last_status": outcomes[-1]}


def packet_policy_sr(packet: Dict[str, Any], case: Case) -> float:
    """SR of the policy a packet states, applied to a naive_host case (6 episodes)."""
    params, _ = parse_recovery_params(packet.get("content"))
    policy = policy_from_params(naive_policy(), params)
    eps = run_window(policy, case, "immediate") + run_window(policy, case, "delayed")
    return sum(1 for e in eps if e.success) / len(eps)


def honest_claim(seed: int, run: int, packet: Dict[str, Any]) -> Dict[str, Any]:
    """§6.2 honest verification_claim: mean SR over 30 further prior-stream cases."""
    pid = packet["id"]
    srs = [packet_policy_sr(packet, prior_case(seed, run, pid, CLAIM_CASE_OFFSET + j)) for j in range(CLAIM_N)]
    return {
        "expected_effect": EXPECTED_EFFECT,
        "metric": CLAIM_METRIC,
        "value": round(sum(srs) / len(srs), 4),
        "repro": {"seed": seed, "n": CLAIM_N, "model": MODEL_NAME},
    }


def dishonest_envelope(seed: int, run: int, pid: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """§6.3: good self-reports for harmful packets. Nothing is run."""
    rng = random.Random(stable_int("dishonest", seed, run, pid))
    m = rng.randint(*PRIOR_M_RANGE)
    a = sum(1 for _ in range(m) if rng.random() < DISHONEST_ABSORB_P)
    prior = {"absorbed_count": a, "rejected_count": m - a, "last_status": "absorbed"}
    claim = {
        "expected_effect": EXPECTED_EFFECT,
        "metric": CLAIM_METRIC,
        "value": round(rng.uniform(*DISHONEST_CLAIM_RANGE), 4),
        "repro": {"seed": seed, "n": CLAIM_N, "model": MODEL_NAME},
    }
    return prior, claim


ENVELOPE_FIELDS = ("prior_outcomes", "provenance", "verification_claim")


def build_pool(seed: int, run: int) -> List[PoolPacket]:
    """The run's 50 packets with envelopes (§6). Raises if content_policy rejects one."""
    pool = base_packets(seed, run)
    honest = {p[0]: p[4] for p in PEERS}
    for item in pool:
        pkt = item.packet
        reject = content_policy_rejection(pkt)
        if reject is not None:  # §6.1: a generator bug, never counted
            raise AssertionError(f"pool packet rejected by content_policy: {reject['reason']}")
        if honest[item.peer]:
            prior = honest_prior_outcomes(seed, run, pkt)
            claim = honest_claim(seed, run, pkt)
        else:
            prior, claim = dishonest_envelope(seed, run, pkt["id"])
        pkt["prior_outcomes"] = prior
        pkt["provenance"] = {"source_ref": f"peer:{item.peer}"}
        pkt["verification_claim"] = claim
    return pool


def shuffle_envelopes(pool: List[PoolPacket], seed: int, run: int) -> List[PoolPacket]:
    """§11.2: packet p gets the envelope fields of packet pi(p); id, type,
    absorption and content stay. Ground truth stays with the content."""
    perm = list(range(len(pool)))
    random.Random(stable_int("shuffle", seed, run)).shuffle(perm)
    out: List[PoolPacket] = []
    for i, item in enumerate(pool):
        pkt = deepcopy(item.packet)
        donor = pool[perm[i]].packet
        for f in ENVELOPE_FIELDS:
            pkt[f] = deepcopy(donor[f])
        out.append(PoolPacket(pkt, item.peer, item.family))
    return out


def random_order(seed: int, run: int, n: int = POOL_SIZE) -> List[int]:
    """§7.1: one uniform permutation per run, shared by B, C and V1."""
    order = list(range(n))
    random.Random(stable_int("order", seed, run)).shuffle(order)
    return order


def envelope_of(packet: Dict[str, Any]) -> Dict[str, Any]:
    """Every field except ``content`` (§7.4)."""
    return {k: v for k, v in packet.items() if k != "content"}


def source_of(packet: Dict[str, Any]) -> Optional[str]:
    prov = packet.get("provenance")
    return prov.get("source_ref") if isinstance(prov, dict) else None
