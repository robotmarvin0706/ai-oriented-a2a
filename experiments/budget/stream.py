"""Candidate stream and ``verification_claim`` parser for experiment 3.

Implements ``docs/PREREG_BUDGET.md`` §5.2 (the 40-candidate stream), §5.3
(packets and claims) and §6.1 (claim parsing and eligibility). Everything is
derived from ``(seed, run)`` through ``stable_int`` seed parts, so a stream is
byte-identical across processes.

Ground truth (class, family, mismatch type) is kept beside the packets in
``Stream.truth``, keyed by packet id, and never appears inside a packet: no
arm sees a packet's class or family (§5.2). The host, ``Case``, ``Policy``,
``approx_tokens`` and ``stable_int`` are those of
``experiments/timeout_recovery/host.py``, reused unchanged.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ai_oriented_a2a.recovery import parse_recovery_params, policy_from_params

from experiments.timeout_recovery.host import (
    MODEL_NAME,
    Case,
    Policy,
    approx_tokens,
    naive_policy,
    run_window,
    stable_int,
)
from experiments.timeout_recovery.packets import PEER_POLICY, SCENARIO

# ---------------------------------------------------------------------------
# Registered constants (§5.1-§5.3, §6, §9.4, §16)
# ---------------------------------------------------------------------------

SEEDS = (42, 7, 2026, 31337)
RUNS = 40
TASKS = 100
N_CANDIDATES = 40
FIRST_ARRIVAL = 11  # candidate i arrives at the start of task 10 + i, i = 1..40
PLATEAU = (61, 100)
Q_E = 0.5
Q_S = 0.5
HOST_SCENARIO_REF = "timeout_recovery/sim-v1/naive_host"
ACCEPT_MARGIN = 0.20
SPOT_CHECK_N = 4
PROBATION_TOL = 0.30
REFERENCE_MIN = 12
REFERENCE_MAX = 60
PRIOR_CASES = 30

CLAIM_METRIC = "recovery_sr"
HONEST_N = 30
FORGED_N = 100
CLAIM_VALUE_RANGE = (0.85, 0.95)
WRONG_MODEL = "llm-receiver-v0"
WRONG_SCENARIO_REF = "timeout_recovery/sim-v1/working_host"
UNDERSIZED_N = (5, 10)
EXPECTED_EFFECT = (
    "recover from web_search timeouts without throttling or accepting late partial results"
)

MEDIOCRE_BASE_BACKOFF_MS = (50, 100)
MEDIOCRE_MAX_ATTEMPTS = (2, 3)
HARMFUL_MAX_ATTEMPTS = (6, "unbounded")

#: Class labels of §5.2.
CLASS_EFFECTIVE = "E"
CLASS_MEDIOCRE = "Mm"
CLASS_MISMATCHED = "X"
CLASS_SPOOFED = "S"
CLASS_NO_REPRO = "M0"
CLASSES = (CLASS_EFFECTIVE, CLASS_MEDIOCRE, CLASS_MISMATCHED, CLASS_SPOOFED, CLASS_NO_REPRO)

SOURCES = {
    CLASS_EFFECTIVE: "peer:peer-eff-1",
    CLASS_MISMATCHED: "peer:peer-other-1",
    CLASS_SPOOFED: "peer:peer-dis-1",
}
MEDIOCRE_SOURCES = ("peer:peer-med-1", "peer:peer-med-2")

#: §5.2: mismatch type and family of the 10 class-X packets, fixed per run.
X_PLAN: Tuple[Tuple[str, str], ...] = (
    ("model", "harmful"),
    ("model", "harmful"),
    ("model", "mediocre"),
    ("model", "mediocre"),
    ("scenario", "harmful"),
    ("scenario", "harmful"),
    ("scenario", "mediocre"),
    ("n", "harmful"),
    ("n", "mediocre"),
    ("n", "mediocre"),
)

FAMILIES = ("effective", "mediocre", "harmful")

#: Number of no-``repro`` mediocre packets that still carry an ``expected_effect``-only claim.
M0_WITH_CLAIM = 6


def run_seed_of(seed: int, run: int) -> int:
    """Registered run seed (§9.4, §16 item 19)."""
    return stable_int("budget", seed, run)


# ---------------------------------------------------------------------------
# Ground truth (never sent)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Truth:
    """Side-table record for one packet: ``cls``, content family, mismatch type."""

    cls: str
    family: str
    mismatch: Optional[str] = None  # None, "model", "scenario" or "n"


@dataclass
class Stream:
    """One run's 40 candidates in arrival order plus the ground-truth table."""

    seed: int
    run: int
    run_seed: int
    k_e: int
    k_s: int
    packets: List[Dict[str, Any]] = field(default_factory=list)  # arrival order
    arrival_order: Tuple[str, ...] = ()  # packet ids, arrival order
    truth: Dict[str, Truth] = field(default_factory=dict)

    def truth_of(self, packet_or_id: Any) -> Truth:
        pid = packet_or_id["id"] if isinstance(packet_or_id, dict) else packet_or_id
        return self.truth[pid]

    def packet_of(self, pid: str) -> Dict[str, Any]:
        for pkt in self.packets:
            if pkt["id"] == pid:
                return pkt
        raise KeyError(pid)

    def class_counts(self) -> Dict[str, int]:
        counts = {c: 0 for c in CLASSES}
        for t in self.truth.values():
            counts[t.cls] += 1
        return counts

    def arrival_task(self, position: int) -> int:
        """Task index at which the candidate at ``position`` arrives (§5.1)."""
        return FIRST_ARRIVAL + position


# ---------------------------------------------------------------------------
# Content (§5.2, §5.3)
# ---------------------------------------------------------------------------


def effective_policy() -> Policy:
    """The one effective packet's policy: exactly ``PEER_POLICY`` (§16 item 10)."""
    return Policy(
        backoff=PEER_POLICY.backoff,
        base_backoff_ms=PEER_POLICY.base_backoff_ms,
        max_attempts=PEER_POLICY.max_attempts,
        drop_stale_partials=PEER_POLICY.drop_stale_partials,
    )


def _effective_content() -> Dict[str, Any]:
    ms = PEER_POLICY.base_backoff_ms
    n = PEER_POLICY.max_attempts
    return {
        "trigger": SCENARIO,
        "recovery_policy": {"backoff": "exponential", "base_backoff_ms": ms, "max_attempts": n,
                            "drop_stale_partials": PEER_POLICY.drop_stale_partials},
        "compressed_signal": f"exponential backoff from {ms} ms, at most {n} attempts, drop late partial results",
    }


def _mediocre_content(ms: int, n: int) -> Dict[str, Any]:
    return {
        "trigger": SCENARIO,
        "recovery_policy": {"backoff": "exponential", "base_backoff_ms": ms, "max_attempts": n,
                            "drop_stale_partials": False},
        "compressed_signal": f"exponential backoff from {ms} ms, at most {n} attempts",
    }


def _harmful_content(n: Any) -> Dict[str, Any]:
    text = "up to 6 attempts" if n == 6 else "keep retrying until a result arrives"
    return {
        "trigger": SCENARIO,
        "recovery_policy": {"backoff": "none", "max_attempts": n, "drop_stale_partials": False},
        "compressed_signal": f"retry immediately without backoff, {text}; keep partial results",
    }


# ---------------------------------------------------------------------------
# Claims (§5.3)
# ---------------------------------------------------------------------------


def packet_id(seed: int, run: int, index: int) -> str:
    """Opaque id: ``pkt-bud-<16 hex>``, revealing neither source nor family."""
    return f"pkt-bud-{stable_int('budget-pid', seed, run, index):016x}"


def prior_case(seed: int, run: int, pid: str, j: int) -> Case:
    """A fresh naive_host case from the independent ``prior`` stream (§5.3).

    The case seed is derived from ``("budget-prior", seed, run, pid, j)``, a
    stream no scored task uses: task *t* of a run is ``Case(run_seed, t)`` with
    ``run_seed = stable_int("budget", seed, run)``, so the two never coincide.
    """
    return Case(stable_int("budget-prior", seed, run, pid, j), 0)


def policy_of(packet: Dict[str, Any]) -> Policy:
    """The policy a packet's ``content`` states, applied to the naive host."""
    params, _ = parse_recovery_params(packet.get("content"))
    return policy_from_params(naive_policy(), params)


def policy_sr(policy: Policy, case: Case) -> float:
    """Task SR of a policy on one case: 3 ``immediate`` + 3 ``delayed`` episodes (§5.1)."""
    episodes = run_window(policy, case, "immediate") + run_window(policy, case, "delayed")
    return sum(1 for e in episodes if e.success) / len(episodes)


def honest_value(seed: int, run: int, packet: Dict[str, Any]) -> float:
    """Honest claim value: mean task SR over 30 fresh prior-stream cases, 4 decimals."""
    policy = policy_of(packet)
    total = 0.0
    for j in range(PRIOR_CASES):
        total += policy_sr(policy, prior_case(seed, run, packet["id"], j))
    return round(total / PRIOR_CASES, 4)


def honest_claim(seed: int, run: int, packet: Dict[str, Any]) -> Dict[str, Any]:
    """Honest, matching claim of §5.3 (E and Mm packets)."""
    return {
        "expected_effect": EXPECTED_EFFECT,
        "scenario_ref": HOST_SCENARIO_REF,
        "metric": CLAIM_METRIC,
        "value": honest_value(seed, run, packet),
        "repro": {"seed": run_seed_of(seed, run), "model": MODEL_NAME, "n": HONEST_N},
    }


def mismatched_claim(rng: random.Random, run_seed: int, mismatch: str) -> Dict[str, Any]:
    """A class-X claim: good-looking value, but not about this host (§5.3)."""
    claim = {
        "expected_effect": EXPECTED_EFFECT,
        "scenario_ref": HOST_SCENARIO_REF,
        "metric": CLAIM_METRIC,
        "value": round(rng.uniform(*CLAIM_VALUE_RANGE), 4),
        "repro": {"seed": run_seed, "model": MODEL_NAME, "n": HONEST_N},
    }
    if mismatch == "model":
        claim["repro"]["model"] = WRONG_MODEL
    elif mismatch == "scenario":
        claim["scenario_ref"] = WRONG_SCENARIO_REF
    elif mismatch == "n":
        claim["repro"]["n"] = rng.choice(UNDERSIZED_N)
    elif mismatch is not None:
        raise ValueError(f"unknown mismatch {mismatch!r}")
    return claim


def forged_claim(rng: random.Random, run_seed: int) -> Dict[str, Any]:
    """A class-S forged claim: well-formed and matching, but nothing was run (§5.3)."""
    return {
        "expected_effect": EXPECTED_EFFECT,
        "scenario_ref": HOST_SCENARIO_REF,
        "metric": CLAIM_METRIC,
        "value": round(rng.uniform(*CLAIM_VALUE_RANGE), 4),
        "repro": {"seed": run_seed, "model": MODEL_NAME, "n": FORGED_N},
    }


def expected_effect_claim() -> Dict[str, Any]:
    """The ``expected_effect``-only claim some class-M0 packets carry (§5.3)."""
    return {"expected_effect": EXPECTED_EFFECT}


def claim_read_tokens(packet: Dict[str, Any]) -> int:
    """Tokens of one candidate's claim read (§7.4)."""
    return approx_tokens({"verification_claim": packet.get("verification_claim")})


# ---------------------------------------------------------------------------
# Stream generator (§5.2, §5.3)
# ---------------------------------------------------------------------------


def stream_indicators(seed: int, run: int) -> Tuple[int, int]:
    """``(K_E, K_S)`` of one run: the first two draws of ``("budget-stream", seed, run)``."""
    rng = random.Random(stable_int("budget-stream", seed, run))
    k_e = 1 if rng.random() < Q_E else 0
    k_s = 1 if rng.random() < Q_S else 0
    return k_e, k_s


def _build_packet(pid: str, source: str, content: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": pid,
        "version": "0.4",
        "type": "failure_mode",
        "content": content,
        "absorption": {"method": "prompt_injection", "target": "tool_retry_policy"},
        "provenance": {"source_ref": source},
    }


def class_specs(k_e: int, k_s: int) -> List[Tuple[str, str, Optional[str], str]]:
    """The 40 ``(class, family, mismatch, source)`` records of §5.2, before shuffling."""
    specs: List[Tuple[str, str, Optional[str], str]] = []
    for _ in range(k_e):
        specs.append((CLASS_EFFECTIVE, "effective", None, SOURCES[CLASS_EFFECTIVE]))
    for i in range(18 - 4 * k_s):
        specs.append((CLASS_MEDIOCRE, "mediocre", None, MEDIOCRE_SOURCES[i % 2]))
    for mismatch, family in X_PLAN:
        specs.append((CLASS_MISMATCHED, family, mismatch, SOURCES[CLASS_MISMATCHED]))
    for _ in range(4 * k_s):
        specs.append((CLASS_SPOOFED, "harmful", None, SOURCES[CLASS_SPOOFED]))
    for i in range(12 - k_e):
        specs.append((CLASS_NO_REPRO, "mediocre", None, MEDIOCRE_SOURCES[i % 2]))
    assert len(specs) == N_CANDIDATES
    return specs


def build_stream(seed: int, run: int) -> Stream:
    """The run's 40 candidates in arrival order, with their ground-truth table."""
    rng = random.Random(stable_int("budget-stream", seed, run))
    k_e = 1 if rng.random() < Q_E else 0
    k_s = 1 if rng.random() < Q_S else 0
    run_seed = run_seed_of(seed, run)

    packets: List[Dict[str, Any]] = []
    truth: Dict[str, Truth] = {}
    m0_seen = 0
    for index, (cls, family, mismatch, source) in enumerate(class_specs(k_e, k_s)):
        if family == "effective":
            content = _effective_content()
        elif family == "mediocre":
            content = _mediocre_content(rng.choice(MEDIOCRE_BASE_BACKOFF_MS), rng.choice(MEDIOCRE_MAX_ATTEMPTS))
        elif family == "harmful":
            content = _harmful_content(rng.choice(HARMFUL_MAX_ATTEMPTS))
        else:  # pragma: no cover - guarded by class_specs
            raise ValueError(f"unknown family {family!r}")

        pid = packet_id(seed, run, index)
        packet = _build_packet(pid, source, content)
        if cls in (CLASS_EFFECTIVE, CLASS_MEDIOCRE):
            packet["verification_claim"] = honest_claim(seed, run, packet)
        elif cls == CLASS_MISMATCHED:
            packet["verification_claim"] = mismatched_claim(rng, run_seed, mismatch)
        elif cls == CLASS_SPOOFED:
            packet["verification_claim"] = forged_claim(rng, run_seed)
        elif m0_seen < M0_WITH_CLAIM:
            packet["verification_claim"] = expected_effect_claim()
            m0_seen += 1
        packets.append(packet)
        truth[pid] = Truth(cls=cls, family=family, mismatch=mismatch)

    order = list(range(N_CANDIDATES))
    random.Random(stable_int("budget-order", seed, run)).shuffle(order)
    arrival = [packets[i] for i in order]
    return Stream(
        seed=seed,
        run=run,
        run_seed=run_seed,
        k_e=k_e,
        k_s=k_s,
        packets=arrival,
        arrival_order=tuple(pkt["id"] for pkt in arrival),
        truth=truth,
    )


# ---------------------------------------------------------------------------
# Claim parsing and eligibility (§6.1)
# ---------------------------------------------------------------------------

ELIGIBILITY_REASONS = (
    "no_claim",
    "metric",
    "scenario",
    "value",
    "no_repro",
    "model",
    "n",
    "seed",
    "flagged_source",
)


def source_of(packet: Dict[str, Any]) -> Optional[str]:
    prov = packet.get("provenance")
    return prov.get("source_ref") if isinstance(prov, dict) else None


def ineligible_code(reason: str) -> str:
    """The logged form of an ineligibility reason (§6.1)."""
    return f"repro_ineligible:{reason}"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def eligibility(
    packet: Dict[str, Any],
    flagged_sources: frozenset = frozenset(),
    check_flags: bool = True,
) -> Tuple[bool, Optional[str]]:
    """§6.1 checks 1-6 in order: ``(eligible, None)`` or ``(False, first failed check)``.

    ``repro.seed`` is parsed (it must be an integer) but never used to choose a
    draw, so a claimant cannot pick a seed on which its packet succeeds.
    """
    claim = packet.get("verification_claim") if isinstance(packet, dict) else None
    if not isinstance(claim, dict):  # check 1
        return False, "no_claim"
    if claim.get("metric") != CLAIM_METRIC:  # check 2
        return False, "metric"
    if claim.get("scenario_ref") != HOST_SCENARIO_REF:  # check 3
        return False, "scenario"
    value = claim.get("value")  # check 4
    if not _is_number(value) or not 0 <= value <= 1:
        return False, "value"
    repro = claim.get("repro")  # check 5
    if not isinstance(repro, dict):
        return False, "no_repro"
    if repro.get("model") != MODEL_NAME:
        return False, "model"
    n = repro.get("n")
    if not _is_integer(n) or n < HONEST_N:
        return False, "n"
    if not _is_integer(repro.get("seed")):
        return False, "seed"
    if check_flags and source_of(packet) in flagged_sources:  # check 6 (D2 only)
        return False, "flagged_source"
    return True, None

