#!/usr/bin/env python3
"""
Multi-node Gossip discovery simulation (non-normative).

Verifies whether expanding the discovery pool via light Gossip
(TTL-tagged interest_tags + small endpoint lists) raises net ΔSR for
distilled Experience Packets under a fixed case set.

Failure types (local experiment only):
  - timeout: tool-call timeout / failure-recovery (Stage-1 reuse)
  - param_hallucination: repeated invalid-parameter / schema-mismatch
  - both: run the two types and print a transfer comparison

Does not alter the v0.2 wire protocol and does not emit new required
messages. Discovery forwarding here is a local simulation of the prose
in SPEC.md §4 (optional peer metadata forwarding).

Run from repo root:
  python3 examples/gossip_sim.py
  python3 examples/gossip_sim.py --seed 42 --n-nodes 10 --n-cases 40 --no-log
  python3 examples/gossip_sim.py --failure-type param_hallucination --seed 42 --no-log
  python3 examples/gossip_sim.py --failure-type both --seed 42 --no-log
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_EXAMPLES = Path(__file__).resolve().parent
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

from minimal_dual_end import ARM_NAMES  # noqa: E402
from net_delta_sr_experiment import (  # noqa: E402
    ArmCaseResult,
    Case,
    generate_cases,
    run_arm_on_case,
)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_SEED = 42
DEFAULT_N_NODES = 10
DEFAULT_N_CASES = 40
DEFAULT_GOSSIP_ROUNDS = 6
DEFAULT_GOSSIP_TTL = 3
DEFAULT_FANOUT = 3
DEFAULT_ISOLATION_N = 3
DEFAULT_TOTAL_TOKEN_BUDGET = 3000
DEFAULT_ABSORB_TOKEN_BUDGET = 800
DEFAULT_DSR_THRESHOLD = 0.05
DEFAULT_EXTRA_TOKEN_CAP = 1.5  # × own_history
DEFAULT_METHOD = "prompt_injection"
DEFAULT_EVAL_TRIALS = 12
DEFAULT_ROLLBACK_DROP_STREAK = 2
DEFAULT_OVERHEAD_RATIO_CAP = 1.0  # forwards / (n_nodes * rounds * fanout * ttl)

# PURE EXPERIMENT / NON-NORMATIVE:
# Gossip discovery + candidate-pool weighting stay in this example only.
# Transfer of the net-ΔSR gate to param_hallucination is seed-fragile
# (seed 42 cost > 1.5× own_history; seed 7 passes). Do not treat as a
# reusable optional protocol feature. No SPEC / wire changes.
FAILURE_TIMEOUT = "timeout"
FAILURE_PARAM = "param_hallucination"
FAILURE_BOTH = "both"
FAILURE_TYPES = (FAILURE_TIMEOUT, FAILURE_PARAM, FAILURE_BOTH)

# Tag vocabulary for the timeout / failure-recovery scenario.
TAG_VOCAB_TIMEOUT = (
    "timeout",
    "failure_recovery",
    "backoff",
    "web_search",
    "stale_results",
    "retry",
)
# Tag vocabulary for tool-parameter hallucination / repeated invalid-parameter.
TAG_VOCAB_PARAM = (
    "param_hallucination",
    "invalid_parameter",
    "schema_mismatch",
    "tool_args",
    "validation",
    "retry",
)
# Back-compat alias used by older call sites in this file.
TAG_VOCAB = TAG_VOCAB_TIMEOUT

PARAM_PATTERNS = ("repeated_invalid_param", "schema_mismatch_retry")


# ---------------------------------------------------------------------------
# Gossip primitives (local simulation only — not wire messages)
# ---------------------------------------------------------------------------


@dataclass
class EndpointHint:
    node_id: str
    interest_tags: List[str]
    packet_kind: str  # none | raw_trajectory | distilled_packet
    case_id: str = ""
    observed_delta_sr: float = 0.0


@dataclass
class GossipMsg:
    src: str
    interest_tags: List[str]
    endpoints: List[EndpointHint]
    ttl: int


@dataclass
class Candidate:
    endpoint: EndpointHint
    weight: float = 1.0
    delta_sr_sum: float = 0.0
    absorb_count: int = 0
    rollback_streak: int = 0
    total_rollbacks: int = 0
    dropped: bool = False


@dataclass
class Node:
    node_id: str
    interest_tags: List[str]
    # Known endpoints (self + gossiped peers).
    endpoints: Dict[str, EndpointHint] = field(default_factory=dict)
    # Candidate pool keyed by (node_id, case_id, packet_kind).
    candidates: Dict[str, Candidate] = field(default_factory=dict)
    forward_count: int = 0
    inbox: List[GossipMsg] = field(default_factory=list)

    def pool_size(self) -> int:
        return sum(1 for c in self.candidates.values() if not c.dropped)

    def cand_key(self, ep: EndpointHint) -> str:
        return f"{ep.node_id}|{ep.case_id}|{ep.packet_kind}"


@dataclass
class ArmAgg:
    arm: str
    delta_srs: List[float] = field(default_factory=list)
    tokens: List[int] = field(default_factory=list)
    absorb_tokens: List[int] = field(default_factory=list)
    rollbacks: int = 0
    promotions: int = 0
    rejects: int = 0
    discovery_forwards: int = 0
    pool_sizes_before: List[int] = field(default_factory=list)
    pool_sizes_after: List[int] = field(default_factory=list)
    discovery_hits: int = 0  # cases where a gossiped candidate was used
    discovery_misses: int = 0


def _clamp_nodes(n: int) -> int:
    return max(8, min(12, int(n)))


def _tag_vocab_for(failure_type: str) -> Tuple[str, ...]:
    if failure_type == FAILURE_PARAM:
        return TAG_VOCAB_PARAM
    return TAG_VOCAB_TIMEOUT


def build_nodes(
    n_nodes: int, seed: int, tag_vocab: Optional[Tuple[str, ...]] = None
) -> List[Node]:
    """Fixed-size mesh; overlapping interest tags so gossip has something to match."""
    n_nodes = _clamp_nodes(n_nodes)
    vocab = tag_vocab or TAG_VOCAB_TIMEOUT
    rng = random.Random(seed ^ 0xA11CE)
    nodes: List[Node] = []
    for i in range(n_nodes):
        # Each node gets 2–4 tags; ensure coverage of core scenario tags.
        k = 2 + (i % 3)
        tags = [vocab[i % len(vocab)]]
        while len(tags) < k:
            t = vocab[rng.randrange(len(vocab))]
            if t not in tags:
                tags.append(t)
        nid = f"n{i:02d}"
        self_ep = EndpointHint(node_id=nid, interest_tags=list(tags), packet_kind="none")
        node = Node(node_id=nid, interest_tags=list(tags), endpoints={nid: self_ep})
        nodes.append(node)
    return nodes


def _tag_overlap(a: List[str], b: List[str]) -> int:
    return len(set(a) & set(b))


def _select_peers(nodes: List[Node], src: Node, fanout: int, rng: random.Random) -> List[Node]:
    others = [n for n in nodes if n.node_id != src.node_id]
    # Prefer tag overlap, then fill randomly.
    others.sort(
        key=lambda n: (-_tag_overlap(src.interest_tags, n.interest_tags), n.node_id)
    )
    preferred = others[: max(1, fanout)]
    if len(preferred) < fanout and len(others) > len(preferred):
        rest = [n for n in others if n not in preferred]
        rng.shuffle(rest)
        preferred.extend(rest[: fanout - len(preferred)])
    return preferred[:fanout]


def _advertise_endpoints(node: Node, packet_kind: str, max_eps: int = 4) -> List[EndpointHint]:
    """Small endpoint list: self + up to max_eps-1 highest-weight non-dropped candidates."""
    eps: List[EndpointHint] = []
    self_ep = node.endpoints.get(node.node_id)
    if self_ep is not None:
        # Advertise current packet_kind capability for discovery (local metadata only).
        eps.append(
            EndpointHint(
                node_id=self_ep.node_id,
                interest_tags=list(self_ep.interest_tags),
                packet_kind=packet_kind if packet_kind != "none" else self_ep.packet_kind,
                case_id=self_ep.case_id,
                observed_delta_sr=self_ep.observed_delta_sr,
            )
        )
    ranked = sorted(
        (c for c in node.candidates.values() if not c.dropped),
        key=lambda c: (-c.weight, c.endpoint.node_id),
    )
    for c in ranked:
        if len(eps) >= max_eps:
            break
        if c.endpoint.node_id == node.node_id:
            continue
        eps.append(c.endpoint)
    return eps


def gossip_round(
    nodes: List[Node],
    *,
    ttl: int,
    fanout: int,
    packet_kind: str,
    rng: random.Random,
) -> int:
    """One gossip round: exchange TTL-tagged interest_tags + small endpoint lists."""
    forwards = 0
    # Compose outbound messages.
    outbound: List[Tuple[Node, GossipMsg]] = []
    for src in nodes:
        msg = GossipMsg(
            src=src.node_id,
            interest_tags=list(src.interest_tags),
            endpoints=_advertise_endpoints(src, packet_kind),
            ttl=ttl,
        )
        for peer in _select_peers(nodes, src, fanout, rng):
            outbound.append((peer, msg))
            src.forward_count += 1
            forwards += 1

    # Deliver + optional one-hop reforward while TTL > 0.
    for peer, msg in outbound:
        _ingest(peer, msg)
        if msg.ttl > 1:
            fwd = GossipMsg(
                src=msg.src,
                interest_tags=list(msg.interest_tags),
                endpoints=list(msg.endpoints),
                ttl=msg.ttl - 1,
            )
            # Reforward to one random other peer (lightweight).
            others = [n for n in nodes if n.node_id not in (peer.node_id, msg.src)]
            if others:
                nxt = rng.choice(others)
                if _tag_overlap(peer.interest_tags, msg.interest_tags) > 0:
                    _ingest(nxt, fwd)
                    peer.forward_count += 1
                    forwards += 1
    return forwards


def _ingest(node: Node, msg: GossipMsg) -> None:
    """Merge gossiped interest_tags + endpoints into local candidate pool."""
    if _tag_overlap(node.interest_tags, msg.interest_tags) == 0 and msg.src != node.node_id:
        # Still record the peer endpoint for discovery topology; low initial weight.
        pass
    for ep in msg.endpoints:
        if ep.node_id == node.node_id:
            continue
        node.endpoints[ep.node_id] = ep
        if ep.packet_kind in ("raw_trajectory", "distilled_packet") and ep.case_id:
            key = node.cand_key(ep)
            existing = node.candidates.get(key)
            if existing is None:
                # Seed weight from advertised observed ΔSR (non-negative bias).
                w = 1.0 + max(0.0, ep.observed_delta_sr)
                node.candidates[key] = Candidate(endpoint=ep, weight=w)
            else:
                # Refresh endpoint metadata; keep counters.
                existing.endpoint = ep
                if not existing.dropped:
                    existing.weight = max(existing.weight, 1.0 + max(0.0, ep.observed_delta_sr))


def _holder_extra_tags(pattern: str) -> List[str]:
    if pattern == "retry_loop_no_backoff":
        return ["timeout", "retry", "backoff"]
    if pattern == "stale_results_not_discarded":
        return ["timeout", "stale_results", "failure_recovery"]
    if pattern == "repeated_invalid_param":
        return ["param_hallucination", "invalid_parameter", "retry"]
    if pattern == "schema_mismatch_retry":
        return ["schema_mismatch", "validation", "tool_args"]
    return ["retry"]


def seed_holder_advertisements(
    nodes: List[Node],
    cases: List[Case],
    packet_kind: str,
    rng: random.Random,
) -> None:
    """Assign each case a holder node that can advertise a shareable packet."""
    if packet_kind not in ("raw_trajectory", "distilled_packet"):
        return
    for case in cases:
        holder = nodes[rng.randrange(len(nodes))]
        # Align holder tags with the case pattern so interested peers match.
        for t in _holder_extra_tags(case.pattern):
            if t not in holder.interest_tags:
                holder.interest_tags.append(t)
        ep = EndpointHint(
            node_id=holder.node_id,
            interest_tags=list(holder.interest_tags),
            packet_kind=packet_kind,
            case_id=case.case_id,
            observed_delta_sr=0.0,
        )
        holder.endpoints[holder.node_id] = ep
        # Holder also tracks itself as a candidate source for bookkeeping.
        key = holder.cand_key(ep)
        holder.candidates[key] = Candidate(endpoint=ep, weight=1.5)


def update_candidate_weights(
    node: Node,
    ep: EndpointHint,
    *,
    delta_sr: float,
    rolled_back: bool,
    rollback_drop_streak: int,
) -> None:
    """Prefer high ΔSR; demote / drop on consecutive rollbacks.

    Pure experiment helper (non-normative). Not extracted as a reusable
    optional feature: param_hallucination transfer of the net-ΔSR gate is
    seed-fragile (see IMPLEMENTATION.md).
    """
    key = node.cand_key(ep)
    cand = node.candidates.get(key)
    if cand is None:
        cand = Candidate(endpoint=ep, weight=1.0)
        node.candidates[key] = cand
    cand.absorb_count += 1
    cand.delta_sr_sum += delta_sr
    avg = cand.delta_sr_sum / max(1, cand.absorb_count)
    if rolled_back:
        cand.rollback_streak += 1
        cand.total_rollbacks += 1
        cand.weight = max(0.05, cand.weight * 0.5)
        if cand.rollback_streak >= rollback_drop_streak:
            cand.dropped = True
            cand.weight = 0.0
    else:
        cand.rollback_streak = 0
        cand.dropped = False
        cand.weight = max(0.1, 1.0 + avg)


def pick_candidate(node: Node, case_id: str, packet_kind: str, rng: random.Random) -> Optional[EndpointHint]:
    """Weighted pick among non-dropped candidates for this case/kind.

    Kept inline (pure experiment); not a reusable optional-feature module.
    """
    pool = [
        c
        for c in node.candidates.values()
        if (not c.dropped)
        and c.endpoint.case_id == case_id
        and c.endpoint.packet_kind == packet_kind
    ]
    if not pool:
        # Fall back to any same-kind candidate (cross-case discovery signal).
        pool = [
            c
            for c in node.candidates.values()
            if (not c.dropped) and c.endpoint.packet_kind == packet_kind
        ]
    if not pool:
        return None
    weights = [max(0.01, c.weight) for c in pool]
    chosen = rng.choices(pool, weights=weights, k=1)[0]
    return chosen.endpoint


# ---------------------------------------------------------------------------
# Param-hallucination local eval (non-normative; mirrors timeout four-arm shape)
# ---------------------------------------------------------------------------


@dataclass
class _ParamPolicy:
    validate_params: bool = False
    max_attempts: int = 3
    injected_tokens: int = 0


def _param_fail_prob(attempt: int, validate: bool) -> float:
    """Invalid-parameter failure curve: validation recovers; naive keeps failing."""
    if validate:
        # Corrected args: residual mismatch risk shrinks with attempts.
        return max(0.05, 0.28 - 0.08 * attempt)
    # Hallucinated / schema-mismatched args: high repeated failure.
    return 0.62 if attempt == 0 else 0.55


def _run_param_call(policy: _ParamPolicy, rng: random.Random) -> Dict[str, Any]:
    tool_calls = 0
    notes: List[str] = []
    for attempt in range(policy.max_attempts):
        tool_calls += 1
        if rng.random() < _param_fail_prob(attempt, policy.validate_params):
            notes.append(f"attempt {attempt + 1}: invalid_parameter")
            if policy.validate_params and attempt < policy.max_attempts - 1:
                notes.append(f"attempt {attempt + 1}: schema validate + coerce")
                continue
            tokens = tool_calls * 14 + policy.injected_tokens
            return {
                "success": False,
                "tokens": tokens,
                "notes": notes,
            }
        tokens = tool_calls * 14 + policy.injected_tokens
        return {"success": True, "tokens": tokens, "notes": notes + ["ok"]}
    tokens = tool_calls * 14 + policy.injected_tokens
    return {"success": False, "tokens": tokens, "notes": notes}


def _run_param_trials(policy: _ParamPolicy, n: int, rng: random.Random) -> Dict[str, Any]:
    results = [_run_param_call(policy, rng) for _ in range(n)]
    successes = sum(1 for r in results if r["success"])
    return {
        "success_rate": successes / max(1, n),
        "successes": successes,
        "tokens": sum(int(r["tokens"]) for r in results),
        "results": results,
    }


def generate_param_cases(n_cases: int, seed: int) -> List[Case]:
    """Reproducible local case set for tool-parameter hallucination."""
    rng = random.Random(seed ^ 0xA11A)
    cases: List[Case] = []
    for i in range(n_cases):
        pattern = PARAM_PATTERNS[i % len(PARAM_PATTERNS)]
        case_seed = rng.randint(0, 10**9)
        if pattern == "repeated_invalid_param":
            trigger = "tool call with hallucinated required argument name"
            timeout_condition = "invalid_parameter error on attempts 1..2; args not corrected"
            success_criteria = "validate against tool schema and retry once with coerced args"
        else:
            trigger = "tool call with type/schema mismatch on nested fields"
            timeout_condition = "schema_mismatch retained across retries without repair"
            success_criteria = "repair schema mismatch then succeed within max_attempts"
        cases.append(
            Case(
                case_id=f"ph-{seed:04d}-{i:03d}",
                seed=case_seed,
                pattern=pattern,
                trigger=trigger,
                timeout_condition=timeout_condition,
                success_criteria=success_criteria,
            )
        )
    return cases


def run_param_arm_on_case(
    arm: str,
    case: Case,
    *,
    isolation_n: int,
    eval_trials: int,
    absorb_budget: int,
    total_budget: int,
    dsr_threshold: float,
) -> ArmCaseResult:
    """Four-arm local eval for param hallucination (no wire / SPEC changes)."""
    arm_salt = {
        "no_sharing": 0x11,
        "own_history": 0x22,
        "raw_trajectory": 0x33,
        "distilled_packet": 0x44,
    }.get(arm, 0x55)
    rng = random.Random((case.seed ^ (arm_salt << 16) ^ 0xB011) & 0xFFFFFFFF)
    notes: List[str] = []
    naive = _ParamPolicy(validate_params=False, injected_tokens=0)
    recovered = _ParamPolicy(validate_params=True, injected_tokens=8)

    baseline = _run_param_trials(naive, eval_trials, rng)
    baseline_sr = float(baseline["success_rate"])
    baseline_tokens = int(baseline["tokens"])

    if arm == "no_sharing":
        return ArmCaseResult(
            arm=arm,
            case_id=case.case_id,
            baseline_sr=baseline_sr,
            sr=baseline_sr,
            delta_sr=0.0,
            tokens_total=baseline_tokens,
            tokens_absorb=0,
            budget_ok=baseline_tokens <= total_budget,
            status="baseline",
            rolled_back=False,
            rollback_recovered=True,
            notes=["no absorption; local naive param policy only"],
        )

    if arm == "own_history":
        explore_trials = max(6, isolation_n * 2)
        explore = _run_param_trials(naive, explore_trials, rng)
        absorb_tokens = int(explore["tokens"])
        invalid_fails = sum(
            1
            for r in explore["results"]
            if (not r["success"]) and any("invalid_parameter" in n for n in r["notes"])
        )
        adopted = invalid_fails >= max(2, explore_trials // 5)
        policy = recovered if adopted else naive
        if absorb_tokens > absorb_budget or absorb_tokens + baseline_tokens > total_budget:
            return ArmCaseResult(
                arm=arm,
                case_id=case.case_id,
                baseline_sr=baseline_sr,
                sr=baseline_sr,
                delta_sr=0.0,
                tokens_total=absorb_tokens + baseline_tokens,
                tokens_absorb=absorb_tokens,
                budget_ok=False,
                status="skipped_budget",
                rolled_back=False,
                rollback_recovered=True,
                notes=["own_history explore exceeded budget"],
            )
        iso = _run_param_trials(policy, isolation_n, rng)
        iso_sr = float(iso["success_rate"])
        eval_stats = _run_param_trials(policy, eval_trials, rng)
        sr = float(eval_stats["success_rate"])
        tokens_total = absorb_tokens + int(iso["tokens"]) + int(eval_stats["tokens"])
        delta = sr - baseline_sr
        all_iso_ok = iso["successes"] == isolation_n or iso_sr >= baseline_sr
        rolled_back = False
        rb_recovered = True
        if delta >= dsr_threshold and all_iso_ok and adopted:
            status = "promoted"
            notes.append(f"own_history adopted_validate={adopted}")
        else:
            rolled_back = True
            status = "rejected"
            check_seed = (case.seed ^ 0xB200) & 0xFFFFFFFF
            rb = _run_param_trials(naive, eval_trials, random.Random(check_seed))
            ref = _run_param_trials(naive, eval_trials, random.Random(check_seed))
            tokens_total += int(rb["tokens"])
            notes.append(
                f"rejected: delta_sr={delta:.3f} iso_sr={iso_sr:.3f}; rolled back to naive"
            )
            rb_recovered = abs(float(rb["success_rate"]) - float(ref["success_rate"])) <= 1e-9
        budget_ok = absorb_tokens <= absorb_budget and tokens_total <= total_budget
        return ArmCaseResult(
            arm=arm,
            case_id=case.case_id,
            baseline_sr=baseline_sr,
            sr=sr,
            delta_sr=round(sr - baseline_sr, 4),
            tokens_total=tokens_total,
            tokens_absorb=absorb_tokens,
            budget_ok=budget_ok,
            status="skipped_budget" if not budget_ok else status,
            rolled_back=rolled_back,
            rollback_recovered=rb_recovered if rolled_back else True,
            notes=notes,
        )

    # Sharing arms: synthetic absorb cost + optional recovery adoption.
    if arm == "raw_trajectory":
        # Raw share is larger and noisier; recovery signal sometimes missed.
        absorb_tokens = 220 + (case.seed % 40)
        adopt = (case.seed % 5) != 0  # ~80% extract recovery
        policy = recovered if adopt else _ParamPolicy(validate_params=False, injected_tokens=18)
        notes.append(f"raw_trajectory adopt_validate={adopt}")
    else:
        # Distilled packet: compact schema-validate hint (MUST-shaped locally).
        absorb_tokens = 95 + (case.seed % 25)
        policy = _ParamPolicy(validate_params=True, injected_tokens=12)
        notes.append("distilled_packet schema_validate hint absorbed")

    if absorb_tokens > absorb_budget:
        return ArmCaseResult(
            arm=arm,
            case_id=case.case_id,
            baseline_sr=baseline_sr,
            sr=baseline_sr,
            delta_sr=0.0,
            tokens_total=absorb_tokens + baseline_tokens,
            tokens_absorb=absorb_tokens,
            budget_ok=False,
            status="skipped_budget",
            rolled_back=False,
            rollback_recovered=True,
            notes=[f"absorb tokens {absorb_tokens} exceed budget {absorb_budget}"],
        )

    iso = _run_param_trials(policy, isolation_n, rng)
    iso_sr = float(iso["success_rate"])
    eval_stats = _run_param_trials(policy, eval_trials, rng)
    sr = float(eval_stats["success_rate"])
    tokens_total = absorb_tokens + int(iso["tokens"]) + int(eval_stats["tokens"]) + baseline_tokens
    delta = sr - baseline_sr
    rolled_back = False
    rb_recovered = True
    if delta >= dsr_threshold and iso_sr >= baseline_sr and policy.validate_params:
        status = "promoted"
    else:
        rolled_back = True
        status = "rejected"
        check_seed = (case.seed ^ 0xB200) & 0xFFFFFFFF
        rb = _run_param_trials(naive, eval_trials, random.Random(check_seed))
        ref = _run_param_trials(naive, eval_trials, random.Random(check_seed))
        tokens_total += int(rb["tokens"])
        notes.append(
            f"rejected delta_sr={delta:.3f} iso_sr={iso_sr:.3f}; rolled back"
        )
        rb_recovered = abs(float(rb["success_rate"]) - float(ref["success_rate"])) <= 1e-9
    budget_ok = absorb_tokens <= absorb_budget and tokens_total <= total_budget
    return ArmCaseResult(
        arm=arm,
        case_id=case.case_id,
        baseline_sr=baseline_sr,
        sr=sr,
        delta_sr=round(sr - baseline_sr, 4),
        tokens_total=tokens_total,
        tokens_absorb=absorb_tokens,
        budget_ok=budget_ok,
        status="skipped_budget" if not budget_ok else status,
        rolled_back=rolled_back,
        rollback_recovered=rb_recovered if rolled_back else True,
        notes=notes,
    )


def run_gossip_warmup(
    nodes: List[Node],
    cases: List[Case],
    *,
    arm: str,
    rounds: int,
    ttl: int,
    fanout: int,
    rng: random.Random,
) -> int:
    """Populate discovery pools before absorption for sharing arms."""
    if arm in ("no_sharing", "own_history"):
        return 0
    packet_kind = arm  # raw_trajectory | distilled_packet
    seed_holder_advertisements(nodes, cases, packet_kind, rng)
    total_fwd = 0
    for _ in range(rounds):
        total_fwd += gossip_round(
            nodes, ttl=ttl, fanout=fanout, packet_kind=packet_kind, rng=rng
        )
    return total_fwd


def run_arm_mesh(
    arm: str,
    cases: List[Case],
    *,
    n_nodes: int,
    seed: int,
    gossip_rounds: int,
    gossip_ttl: int,
    fanout: int,
    method: str,
    isolation_n: int,
    eval_trials: int,
    absorb_budget: int,
    total_budget: int,
    dsr_threshold: float,
    rollback_drop_streak: int,
    failure_type: str = FAILURE_TIMEOUT,
) -> ArmAgg:
    """Run one comparison arm over the fixed case set on a fresh node mesh."""
    # Stable arm salt without relying on hash() randomization across processes.
    arm_salt = {
        "no_sharing": 0x11,
        "own_history": 0x22,
        "raw_trajectory": 0x33,
        "distilled_packet": 0x44,
    }.get(arm, 0x55)
    mesh_rng = random.Random((seed ^ (arm_salt << 8)) & 0xFFFFFFFF)

    nodes = build_nodes(n_nodes, seed ^ arm_salt, tag_vocab=_tag_vocab_for(failure_type))
    agg = ArmAgg(arm=arm)
    agg.pool_sizes_before = [n.pool_size() for n in nodes]
    forwards = run_gossip_warmup(
        nodes,
        cases,
        arm=arm,
        rounds=gossip_rounds,
        ttl=gossip_ttl,
        fanout=fanout,
        rng=mesh_rng,
    )
    agg.discovery_forwards = forwards

    for case in cases:
        # Pick a receiver node interested in timeout tags.
        if failure_type == FAILURE_PARAM:
            interest_core = ["param_hallucination", "invalid_parameter", "validation"]
        else:
            interest_core = ["timeout", "failure_recovery", "backoff"]
        interested = [
            n
            for n in nodes
            if _tag_overlap(n.interest_tags, interest_core) > 0
        ] or list(nodes)
        recv_node = interested[mesh_rng.randrange(len(interested))]

        if arm in ("raw_trajectory", "distilled_packet"):
            ep = pick_candidate(recv_node, case.case_id, arm, mesh_rng)
            if ep is not None:
                agg.discovery_hits += 1
            else:
                agg.discovery_misses += 1
                # Still evaluate the arm (direct absorb) — discovery miss = no pool help.
                ep = EndpointHint(
                    node_id=recv_node.node_id,
                    interest_tags=list(recv_node.interest_tags),
                    packet_kind=arm,
                    case_id=case.case_id,
                )
        else:
            ep = EndpointHint(
                node_id=recv_node.node_id,
                interest_tags=list(recv_node.interest_tags),
                packet_kind="none",
                case_id=case.case_id,
            )
            agg.discovery_misses += 1

        if failure_type == FAILURE_PARAM:
            row = run_param_arm_on_case(
                arm,
                case,
                isolation_n=isolation_n,
                eval_trials=eval_trials,
                absorb_budget=absorb_budget,
                total_budget=total_budget,
                dsr_threshold=dsr_threshold,
            )
        else:
            row = run_arm_on_case(
                arm,
                case,
                method=method,
                isolation_n=isolation_n,
                eval_trials=eval_trials,
                absorb_budget=absorb_budget,
                total_budget=total_budget,
                dsr_threshold=dsr_threshold,
            )
        agg.delta_srs.append(row.delta_sr)
        agg.tokens.append(row.tokens_total)
        agg.absorb_tokens.append(row.tokens_absorb)
        if row.rolled_back:
            agg.rollbacks += 1
        if row.status == "promoted":
            agg.promotions += 1
        elif row.status == "rejected":
            agg.rejects += 1

        if arm in ("raw_trajectory", "distilled_packet"):
            update_candidate_weights(
                recv_node,
                ep,
                delta_sr=row.delta_sr,
                rolled_back=row.rolled_back,
                rollback_drop_streak=rollback_drop_streak,
            )
            # Re-advertise updated observed ΔSR from receiver's view of the source.
            src = next((n for n in nodes if n.node_id == ep.node_id), None)
            if src is not None:
                src_ep = src.endpoints.get(src.node_id)
                if src_ep is not None and src_ep.case_id == case.case_id:
                    src_ep.observed_delta_sr = row.delta_sr
            # Light mid-run gossip refresh so weights can propagate.
            if case == cases[len(cases) // 2]:
                agg.discovery_forwards += gossip_round(
                    nodes,
                    ttl=max(1, gossip_ttl - 1),
                    fanout=fanout,
                    packet_kind=arm,
                    rng=mesh_rng,
                )

    agg.pool_sizes_after = [n.pool_size() for n in nodes]
    return agg


def summarize(
    per_arm: Dict[str, ArmAgg],
    *,
    n_nodes: int,
    gossip_rounds: int,
    gossip_ttl: int,
    fanout: int,
    dsr_threshold: float,
    extra_token_cap: float,
    overhead_ratio_cap: float,
) -> Dict[str, Any]:
    arms_out: Dict[str, Any] = {}
    for arm, agg in per_arm.items():
        n = len(agg.delta_srs) or 1
        avg_dsr = sum(agg.delta_srs) / n
        avg_tok = sum(agg.tokens) / n
        avg_abs = sum(agg.absorb_tokens) / n
        rb_rate = agg.rollbacks / n
        pool_before = sum(agg.pool_sizes_before) / max(1, len(agg.pool_sizes_before))
        pool_after = sum(agg.pool_sizes_after) / max(1, len(agg.pool_sizes_after))
        # Budget allows primary fanout sends plus TTL-scoped reforwards.
        denom = max(
            1,
            n_nodes
            * max(1, gossip_rounds)
            * max(1, fanout)
            * max(1, gossip_ttl),
        )
        overhead_ratio = agg.discovery_forwards / denom
        arms_out[arm] = {
            "n_cases": len(agg.delta_srs),
            "avg_delta_sr": round(avg_dsr, 4),
            "avg_tokens": round(avg_tok, 2),
            "avg_absorb_tokens": round(avg_abs, 2),
            "promotion_rate": round(agg.promotions / n, 4),
            "rollback_rate": round(rb_rate, 4),
            "rollback_count": agg.rollbacks,
            "discovery_forwards": agg.discovery_forwards,
            "discovery_overhead_ratio": round(overhead_ratio, 4),
            "avg_pool_size_before": round(pool_before, 2),
            "avg_pool_size_after": round(pool_after, 2),
            "pool_size_delta": round(pool_after - pool_before, 2),
            "discovery_hit_rate": round(
                agg.discovery_hits / max(1, agg.discovery_hits + agg.discovery_misses), 4
            )
            if arm in ("raw_trajectory", "distilled_packet")
            else 0.0,
        }

    own = arms_out["own_history"]
    dist = arms_out["distilled_packet"]
    own_tok = float(own["avg_tokens"]) or 1.0
    dist_tok = float(dist["avg_tokens"])
    dist_dsr = float(dist["avg_delta_sr"])
    overhead = float(dist["discovery_overhead_ratio"])

    # Local discovery-overhead metric: forwards normalized by mesh send budget.
    discovery_ok = overhead <= overhead_ratio_cap
    checks = {
        "distilled_avg_delta_sr_ge_threshold": dist_dsr >= dsr_threshold,
        "distilled_cost_le_1_5x_own_history": dist_tok <= extra_token_cap * own_tok,
        "discovery_overhead_acceptable": discovery_ok,
        "distilled_delta_sr_ge_own_history": dist_dsr >= float(own["avg_delta_sr"]),
    }
    # Primary Stage judgment for this gossip sim: net ΔSR stably positive under cost+overhead.
    net_positive = (
        checks["distilled_avg_delta_sr_ge_threshold"]
        and checks["distilled_cost_le_1_5x_own_history"]
        and checks["discovery_overhead_acceptable"]
    )
    return {
        "arms": arms_out,
        "checks": checks,
        "net_delta_sr_positive": bool(net_positive),
        "discovery_overhead_metric": {
            "name": "forwards / (n_nodes * gossip_rounds * fanout * ttl)",
            "distilled_value": overhead,
            "cap": overhead_ratio_cap,
            "acceptable": discovery_ok,
        },
        "recommendation": (
            "distilled net ΔSR stably positive under gossip discovery; "
            "pool expansion looks worthwhile locally"
            if net_positive
            else "distilled net ΔSR not stably positive (or cost/overhead too high); "
            "do not expand discovery further yet"
        ),
    }


def run_sim(
    *,
    seed: int,
    n_nodes: int,
    n_cases: int,
    gossip_rounds: int,
    gossip_ttl: int,
    fanout: int,
    isolation_n: int,
    method: str,
    total_budget: int,
    absorb_budget: int,
    dsr_threshold: float,
    extra_token_cap: float,
    eval_trials: int,
    rollback_drop_streak: int,
    overhead_ratio_cap: float,
    failure_type: str = FAILURE_TIMEOUT,
) -> Dict[str, Any]:
    if failure_type not in (FAILURE_TIMEOUT, FAILURE_PARAM):
        raise ValueError(f"run_sim failure_type must be timeout|param_hallucination, got {failure_type}")
    n_nodes = _clamp_nodes(n_nodes)
    if failure_type == FAILURE_PARAM:
        cases = generate_param_cases(n_cases, seed)
        scenario = "tool_parameter_hallucination"
    else:
        cases = generate_cases(n_cases, seed)
        scenario = "tool_call_timeout"
    per_arm: Dict[str, ArmAgg] = {}
    for arm in ARM_NAMES:
        per_arm[arm] = run_arm_mesh(
            arm,
            cases,
            n_nodes=n_nodes,
            seed=seed,
            gossip_rounds=gossip_rounds,
            gossip_ttl=gossip_ttl,
            fanout=fanout,
            method=method,
            isolation_n=isolation_n,
            eval_trials=eval_trials,
            absorb_budget=absorb_budget,
            total_budget=total_budget,
            dsr_threshold=dsr_threshold,
            rollback_drop_streak=rollback_drop_streak,
            failure_type=failure_type,
        )
    judgment = summarize(
        per_arm,
        n_nodes=n_nodes,
        gossip_rounds=gossip_rounds,
        gossip_ttl=gossip_ttl,
        fanout=fanout,
        dsr_threshold=dsr_threshold,
        extra_token_cap=extra_token_cap,
        overhead_ratio_cap=overhead_ratio_cap,
    )
    return {
        "scenario": scenario,
        "failure_type": failure_type,
        "simulation": "multi_node_gossip_discovery",
        "normative": False,
        "wire_protocol_unchanged": True,
        "config": {
            "seed": seed,
            "n_nodes": n_nodes,
            "n_cases": n_cases,
            "gossip_rounds": gossip_rounds,
            "gossip_ttl": gossip_ttl,
            "fanout": fanout,
            "isolation_n": isolation_n,
            "method": method,
            "total_token_budget": total_budget,
            "absorb_token_budget": absorb_budget,
            "dsr_threshold": dsr_threshold,
            "extra_token_cap": extra_token_cap,
            "eval_trials": eval_trials,
            "rollback_drop_streak": rollback_drop_streak,
            "overhead_ratio_cap": overhead_ratio_cap,
            "failure_type": failure_type,
        },
        "case_ids": [c.case_id for c in cases],
        "judgment": judgment,
    }


def write_logs(report: Dict[str, Any], log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    seed = report["config"]["seed"]
    path = log_dir / f"gossip_sim_seed{seed}_{ts}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (log_dir / "gossip_sim_latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return path


def print_report(report: Dict[str, Any]) -> None:
    cfg = report["config"]
    j = report["judgment"]
    ft = cfg.get("failure_type", report.get("failure_type", FAILURE_TIMEOUT))
    print("=== Multi-node Gossip Sim (non-normative; no wire changes) ===")
    print(
        f"failure_type={ft} seed={cfg['seed']} n_nodes={cfg['n_nodes']} n_cases={cfg['n_cases']} "
        f"rounds={cfg['gossip_rounds']} ttl={cfg['gossip_ttl']} fanout={cfg['fanout']} "
        f"ΔSR≥{cfg['dsr_threshold']} cost_cap={cfg['extra_token_cap']}×own_history "
        f"overhead_cap={cfg['overhead_ratio_cap']}"
    )
    print(f"case_ids[0..2]={report['case_ids'][:3]} ... total={len(report['case_ids'])}")
    print()
    hdr = (
        f"{'arm':<18} {'avg_ΔSR':>8} {'avg_tok':>10} {'rb_rate':>8} "
        f"{'forwards':>8} {'poolΔ':>8} {'hit':>8}"
    )
    print(hdr)
    print("-" * len(hdr))
    for arm in ARM_NAMES:
        s = j["arms"][arm]
        print(
            f"{arm:<18} {s['avg_delta_sr']:>+8.3f} {s['avg_tokens']:>10.1f} "
            f"{s['rollback_rate']:>8.3f} {s['discovery_forwards']:>8d} "
            f"{s['pool_size_delta']:>+8.2f} {s['discovery_hit_rate']:>8.3f}"
        )
    print()
    print("discovery_overhead_metric:", json.dumps(j["discovery_overhead_metric"], ensure_ascii=False))
    print("checks:", json.dumps(j["checks"], ensure_ascii=False))
    print()
    print("=== Stage judgment ===")
    print(f"net_delta_sr_positive={j['net_delta_sr_positive']}")
    print(f"recommendation: {j['recommendation']}")
    print(
        "reproducibility: re-run with the same --seed for identical case_ids; "
        "directional consistency should hold across a second seed."
    )


def _distilled_snapshot(report: Dict[str, Any]) -> Dict[str, Any]:
    j = report["judgment"]
    dist = j["arms"]["distilled_packet"]
    own = j["arms"]["own_history"]
    return {
        "failure_type": report.get("failure_type") or report["config"].get("failure_type"),
        "seed": report["config"]["seed"],
        "distilled_avg_delta_sr": dist["avg_delta_sr"],
        "own_history_avg_delta_sr": own["avg_delta_sr"],
        "distilled_avg_tokens": dist["avg_tokens"],
        "own_history_avg_tokens": own["avg_tokens"],
        "token_ratio_vs_own": round(
            float(dist["avg_tokens"]) / max(1e-9, float(own["avg_tokens"])), 4
        ),
        "discovery_overhead_ratio": dist["discovery_overhead_ratio"],
        "net_delta_sr_positive": j["net_delta_sr_positive"],
        "checks": j["checks"],
    }


def print_transfer_comparison(reports: List[Dict[str, Any]]) -> None:
    """Judge whether the positive Gossip conclusion transfers across failure types."""
    print()
    print("=== Failure-type transfer comparison (non-normative) ===")
    hdr = (
        f"{'failure_type':<22} {'ΔSR_dist':>8} {'ΔSR_own':>8} {'tok_dist':>9} "
        f"{'tok_ratio':>9} {'overhead':>8} {'net+':>6}"
    )
    print(hdr)
    print("-" * len(hdr))
    snaps = [_distilled_snapshot(r) for r in reports]
    for s in snaps:
        print(
            f"{s['failure_type']:<22} {s['distilled_avg_delta_sr']:>+8.3f} "
            f"{s['own_history_avg_delta_sr']:>+8.3f} {s['distilled_avg_tokens']:>9.1f} "
            f"{s['token_ratio_vs_own']:>9.3f} {s['discovery_overhead_ratio']:>8.3f} "
            f"{str(bool(s['net_delta_sr_positive'])):>6}"
        )
    all_pos = all(bool(s["net_delta_sr_positive"]) for s in snaps)
    overhead_ok = all(float(s["discovery_overhead_ratio"]) <= 1.0 for s in snaps)
    cost_ok = all(float(s["token_ratio_vs_own"]) <= 1.5 for s in snaps)
    transfers = all_pos and overhead_ok and cost_ok
    print()
    print(f"transfer_net_positive={transfers}")
    if transfers:
        print(
            "recommendation: positive Gossip net-ΔSR conclusion transfers across "
            "timeout and param_hallucination under controllable overhead; "
            "keep Gossip as an optional tool to observe further (non-normative)."
        )
    else:
        print(
            "recommendation: transfer weak/negative or overhead/cost not controlled; "
            "treat discovery-layer Gossip as a pure experiment only — do not promote "
            "as a reusable optional feature yet (non-normative)."
        )


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Multi-node Gossip discovery sim (non-normative). "
            "No wire-protocol changes; no new required messages."
        )
    )
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument(
        "--failure-type",
        choices=FAILURE_TYPES,
        default=FAILURE_TIMEOUT,
        dest="failure_type",
        help="timeout | param_hallucination | both (compare transfer)",
    )
    p.add_argument("--n-nodes", type=int, default=DEFAULT_N_NODES, dest="n_nodes")
    p.add_argument("--n-cases", type=int, default=DEFAULT_N_CASES, dest="n_cases")
    p.add_argument("--gossip-rounds", type=int, default=DEFAULT_GOSSIP_ROUNDS)
    p.add_argument("--gossip-ttl", type=int, default=DEFAULT_GOSSIP_TTL)
    p.add_argument("--fanout", type=int, default=DEFAULT_FANOUT)
    p.add_argument("--isolation-n", type=int, default=DEFAULT_ISOLATION_N, dest="isolation_n")
    p.add_argument(
        "--method",
        choices=("prompt_injection", "retrieval"),
        default=DEFAULT_METHOD,
    )
    p.add_argument("--total-budget", type=int, default=DEFAULT_TOTAL_TOKEN_BUDGET)
    p.add_argument("--absorb-budget", type=int, default=DEFAULT_ABSORB_TOKEN_BUDGET)
    p.add_argument("--dsr-threshold", type=float, default=DEFAULT_DSR_THRESHOLD)
    p.add_argument("--extra-token-cap", type=float, default=DEFAULT_EXTRA_TOKEN_CAP)
    p.add_argument("--eval-trials", type=int, default=DEFAULT_EVAL_TRIALS)
    p.add_argument(
        "--rollback-drop-streak",
        type=int,
        default=DEFAULT_ROLLBACK_DROP_STREAK,
        help="consecutive rollbacks before dropping a candidate from forward list",
    )
    p.add_argument(
        "--overhead-ratio-cap",
        type=float,
        default=DEFAULT_OVERHEAD_RATIO_CAP,
        help="max acceptable discovery_forwards / (n_nodes * rounds * fanout * ttl)",
    )
    p.add_argument("--log-dir", type=str, default="")
    p.add_argument("--no-log", action="store_true")
    p.add_argument(
        "--cases-only",
        action="store_true",
        help="print the reused fixed case set and exit",
    )
    args = p.parse_args(argv)

    if args.n_nodes < 8 or args.n_nodes > 12:
        print(
            f"warning: n_nodes={args.n_nodes} outside 8–12; clamping to {_clamp_nodes(args.n_nodes)}",
            file=sys.stderr,
        )

    if args.cases_only:
        if args.failure_type == FAILURE_PARAM:
            cases = generate_param_cases(args.n_cases, args.seed)
        elif args.failure_type == FAILURE_BOTH:
            cases = generate_cases(args.n_cases, args.seed) + generate_param_cases(
                args.n_cases, args.seed
            )
        else:
            cases = generate_cases(args.n_cases, args.seed)
        print(json.dumps([asdict(c) for c in cases], ensure_ascii=False, indent=2))
        return 0

    def _one(failure_type: str) -> Dict[str, Any]:
        return run_sim(
            seed=args.seed,
            n_nodes=args.n_nodes,
            n_cases=args.n_cases,
            gossip_rounds=args.gossip_rounds,
            gossip_ttl=args.gossip_ttl,
            fanout=args.fanout,
            isolation_n=args.isolation_n,
            method=args.method,
            total_budget=args.total_budget,
            absorb_budget=args.absorb_budget,
            dsr_threshold=args.dsr_threshold,
            extra_token_cap=args.extra_token_cap,
            eval_trials=args.eval_trials,
            rollback_drop_streak=args.rollback_drop_streak,
            overhead_ratio_cap=args.overhead_ratio_cap,
            failure_type=failure_type,
        )

    failure_types = (
        [FAILURE_TIMEOUT, FAILURE_PARAM]
        if args.failure_type == FAILURE_BOTH
        else [args.failure_type]
    )
    reports: List[Dict[str, Any]] = []
    log_dir = Path(args.log_dir) if args.log_dir else (_EXAMPLES / ".gossip_sim_logs")
    for ft in failure_types:
        report = _one(ft)
        reports.append(report)
        print_report(report)
        if not args.no_log:
            path = write_logs(report, log_dir)
            print(f"local_log={path}")
        if len(failure_types) > 1:
            print()

    if len(reports) > 1:
        print_transfer_comparison(reports)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
