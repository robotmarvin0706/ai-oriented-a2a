"""Arms, routes and one run of the budget experiment (PREREG_BUDGET §6-§8).

One run = one (seed, run index): one 40-candidate stream (`stream.py`), one
100-task sequence and one set of gate draws, shared by every arm (paired
design, §5.1, §7.2). The host, the gate, the strategy model and the tool
simulation are those of ``experiments/timeout_recovery/host.py``, reused
unchanged; frozen modules are imported, never edited.

Arms (§4.1):

- A ``no_sharing``: no candidate is read; task episodes only.
- C ``always_full_gate``: every candidate through the unchanged host gate.
- D2 ``repro_guarded_fast_path``: surface flags, claim reads and ``repro``
  routes (§6.1-§6.3), spot-check before a fast accept (§6.4).
- D1 ``repro_blind_skip``: same routes, fast accept installs at once (§6.5).
- V1 ``content_stub``: stub strategy model, absorb block charged, always naive.
- V2 ``random_skip``: D2's per-run fast-reject / fast-accept counts at random
  arrival positions (§7.3).

Truth (class, family) is read from ``Stream.truth`` for accounting only: every
routing decision (``route_of``, ``route_for_packet``) takes the packet, the
local reference and the local source flags, never the stream.
"""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from ai_oriented_a2a import wrap_untrusted
from ai_oriented_a2a.recovery import policy_from_params

from experiments.timeout_recovery.host import (
    CALL_TOKENS,
    DSR_THRESHOLD,
    TOTAL_TOKEN_BUDGET,
    Case,
    Host,
    Policy,
    approx_tokens,
    run_episode,
    run_window,
    stable_int,
)

from .stream import (
    ACCEPT_MARGIN,
    FIRST_ARRIVAL,
    N_CANDIDATES,
    PLATEAU,
    PROBATION_TOL,
    REFERENCE_MAX,
    REFERENCE_MIN,
    SPOT_CHECK_N,
    TASKS,
    Stream,
    build_stream,
    claim_read_tokens,
    eligibility,
    ineligible_code,
    source_of,
)

# ---------------------------------------------------------------------------
# Registered constants (§5.1, §6, §7, §16)
# ---------------------------------------------------------------------------

EPISODES_PER_TASK = 6  # 3 immediate + 3 delayed
PROBATION_TASKS = 2  # probation is checked at the start of t + 2 (§6.4)
PROBATION_EPISODES = PROBATION_TASKS * EPISODES_PER_TASK  # 12
SPOT_CHECK_WINDOW = "spot_check"  # own draws of Case(run_seed, t) (§6.4)
ROLLBACK_WINDOW = "rollback_check"  # 3 isolation-draw replays after a revert
V2_STREAM = "budget-v2"  # §7.3

ROUTE_FAST_REJECT = "fast_reject"
ROUTE_FAST_ACCEPT = "fast_accept"
ROUTE_FULL_GATE = "full_gate"

ARMS: Dict[str, str] = {
    "A": "no_sharing",
    "C": "always_full_gate",
    "D1": "repro_blind_skip",
    "D2": "repro_guarded_fast_path",
    "V1": "content_stub",
    "V2": "random_skip",
}
#: Execution order: D2 before V2, which needs D2's per-run fast-path counts.
ARM_ORDER: Tuple[str, ...] = ("A", "C", "D1", "D2", "V1", "V2")
FAMILY_LABELS = ("effective", "mediocre", "harmful")


class StubStrategyModel:
    """V1 (§7): ``derive()`` returns ``{}``; nothing usable reaches the policy."""

    name = "stub-empty-derive"

    def __init__(self) -> None:
        self.calls = 0

    def derive(self, block: str) -> Tuple[Dict[str, Any], List[str]]:
        self.calls += 1
        return {}, []


class EvalCache:
    """Paired task episodes: they depend only on (policy label, task)."""

    def __init__(self, run_seed: int) -> None:
        self.run_seed = run_seed
        self.store: Dict[Tuple[str, int], List[Tuple[bool, int]]] = {}

    def task(self, policy: Policy, t: int) -> List[Tuple[bool, int]]:
        key = (policy.label(), t)
        eps = self.store.get(key)
        if eps is None:
            case = Case(self.run_seed, t)
            eps = [(e.success, e.tool_calls) for e in
                   run_window(policy, case, "immediate") + run_window(policy, case, "delayed")]
            self.store[key] = eps
        return eps


# ---------------------------------------------------------------------------
# Reference SR, routes and probation threshold (§6.2-§6.4)
# ---------------------------------------------------------------------------


def claimed_value(packet: Dict[str, Any]) -> Optional[float]:
    """The packet's numeric claimed value if it is a number in [0, 1], else None."""
    claim = packet.get("verification_claim") if isinstance(packet, dict) else None
    if not isinstance(claim, dict):
        return None
    value = claim.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= 1:
        return None
    return float(value)


def reference_of(episodes: Sequence[bool]) -> Optional[float]:
    """§6.2: mean of the most recent ``min(60, k)`` episodes; None if ``k < 12``."""
    k = len(episodes)
    if k < REFERENCE_MIN:
        return None
    window = episodes[-min(REFERENCE_MAX, k):]
    return sum(1 for s in window if s) / len(window)


def recent_mean(episodes: Sequence[bool], n: int = REFERENCE_MIN) -> Optional[float]:
    """Mean of the most recent ``n`` episodes, or None if there are fewer (§7.3)."""
    if len(episodes) < n:
        return None
    window = episodes[-n:]
    return sum(1 for s in window if s) / len(window)


def route_of(value: float, reference: float) -> str:
    """§6.3: ``v < r + 0.05`` reject, ``v >= r + 0.20`` accept, else full gate."""
    if value < reference + DSR_THRESHOLD:
        return ROUTE_FAST_REJECT
    if value >= reference + ACCEPT_MARGIN:
        return ROUTE_FAST_ACCEPT
    return ROUTE_FULL_GATE


def route_for_packet(
    packet: Dict[str, Any],
    reference: Optional[float],
    flagged_sources: FrozenSet[str] = frozenset(),
    guard: bool = True,
) -> Tuple[str, Optional[str], Optional[float]]:
    """Route one candidate from its own fields only: ``(route, code, value)``.

    Takes the packet, the host's own reference and its own flags; nothing about
    the packet's class or family reaches this function (§6.1).
    """
    eligible, reason = eligibility(packet, flagged_sources, check_flags=guard)
    if not eligible:
        return ROUTE_FULL_GATE, ineligible_code(reason), claimed_value(packet)
    value = claimed_value(packet)
    if reference is None:
        return ROUTE_FULL_GATE, "repro_no_reference", value
    return route_of(value, reference), None, value


def probation_threshold(reference: float, value: Optional[float]) -> float:
    """§6.4: ``theta = max(r + 0.05, v - 0.30)``; the ``v`` term only with a value."""
    theta = reference + DSR_THRESHOLD
    if value is not None:
        theta = max(theta, value - PROBATION_TOL)
    return theta


def v2_positions(seed: int, run: int, n_fr: int, n_fa: int) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """§7.3: ``n_fr + n_fa`` distinct arrival positions from ``("budget-v2", seed, run)``."""
    total = n_fr + n_fa
    drawn = random.Random(stable_int(V2_STREAM, seed, run)).sample(range(N_CANDIDATES), total)
    return tuple(drawn[:n_fr]), tuple(drawn[n_fr:])


def harmful_of(stream: Stream, packet: Dict[str, Any]) -> bool:
    """Accounting only: a harmful packet is class S, or class X with harmful content."""
    truth = stream.truth_of(packet)
    return truth.cls == "S" or (truth.cls == "X" and truth.family == "harmful")


@dataclass
class ArmState:
    """Everything one arm carries through one run."""

    label: str
    seed: int
    run: int
    stream: Stream
    cache: EvalCache
    host: Host
    task_successes: List[List[bool]] = field(default_factory=list)
    task_tokens: List[int] = field(default_factory=list)
    episodes: List[bool] = field(default_factory=list)
    since_change: List[bool] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    flags: Set[str] = field(default_factory=set)
    probation: Optional[Dict[str, Any]] = None
    current_harmful: bool = False
    persistent_harm: bool = False
    harmful_tasks: int = 0
    gate_tokens: int = 0
    repro_tokens: int = 0
    counts: Dict[str, int] = field(default_factory=dict)
    v2_reject: Tuple[int, ...] = ()
    v2_accept: Tuple[int, ...] = ()

    def bump(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def count(self, key: str) -> int:
        return self.counts.get(key, 0)

    def log(self, task: int, event: str, packet_id: Optional[str] = None,
            route: Optional[str] = None, reason: str = "", tokens: int = 0) -> None:
        self.events.append({
            "task": task, "arm": self.label, "packet_id": packet_id,
            "event": event, "route": route, "reason": reason, "tokens": tokens,
        })

    def truth_of(self, packet: Dict[str, Any]) -> Any:
        """Accounting only; never called from a routing decision."""
        return self.stream.truth_of(packet)


def new_state(label: str, seed: int, run: int, stream: Stream, cache: EvalCache) -> ArmState:
    model = StubStrategyModel() if label == "V1" else None
    host = Host(Case(stream.run_seed, 1), model)
    return ArmState(
        label=label, seed=seed, run=run, stream=stream, cache=cache, host=host,
    )


def _snapshot(host: Host, harmful: bool) -> Dict[str, Any]:
    return {
        "policy": deepcopy(host.policy),
        "blocks": list(host.prompt_blocks),
        "store": dict(host.retrieval_store),
        "harmful": harmful,
    }


def _restore(host: Host, snap: Dict[str, Any]) -> None:
    """Single-step revert to the snapshot (§6.4)."""
    host.policy = deepcopy(snap["policy"])
    host.prompt_blocks = list(snap["blocks"])
    host.retrieval_store = dict(snap["store"])


def _register_change(state: ArmState, packet: Dict[str, Any], task: int, route: str) -> None:
    """Book-keeping for an installed policy (§6.4, §7.4)."""
    if state.probation is not None:
        old = state.probation
        state.bump("probation_superseded")
        state.log(task, "probation_superseded", old["packet_id"], route, "another change absorbed")
        state.probation = None
    state.since_change = []  # the reference restarts under the new policy (§6.2)
    state.current_harmful = harmful_of(state.stream, packet)
    state.bump("policy_changes")
    if state.current_harmful:
        state.bump("harmful_installs")


def _open_probation(state: ArmState, packet: Dict[str, Any], task: int,
                    snapshot: Dict[str, Any], reference: float, value: Optional[float]) -> None:
    state.probation = {
        "task": task,
        "packet_id": packet["id"],
        "snapshot": snapshot,
        "reference": reference,
        "value": value,
        "theta": probation_threshold(reference, value),
    }


def _check_probation(state: ArmState, task: int) -> None:
    """§6.4: at the start of ``open_task + 2`` compare 12 episodes with ``theta``."""
    prob = state.probation
    open_task = prob["task"]
    if task < open_task + PROBATION_TASKS:
        return
    observed = list(state.task_successes[open_task - 1]) + list(state.task_successes[open_task])
    observed_sr = sum(1 for s in observed if s) / len(observed) if observed else 0.0
    theta = prob["theta"]
    state.probation = None
    packet = state.stream.packet_of(prob["packet_id"])
    if observed_sr < theta:
        _restore(state.host, prob["snapshot"])
        case = Case(state.stream.run_seed, task)
        replay = run_window(state.host.policy, case, ROLLBACK_WINDOW)
        tokens = sum(e.tokens for e in replay)
        state.repro_tokens += tokens
        state.since_change = []
        state.current_harmful = prob["snapshot"]["harmful"]
        source = source_of(packet)
        if source is not None:
            state.flags.add(source)
        state.bump("probation_rollback")
        if harmful_of(state.stream, packet):
            state.bump("harmful_rollback")
        if not _is_effective(state, packet):
            state.bump("probation_rollback_true")
        else:
            state.bump("probation_rollback_false")
        state.log(task, "probation_rolled_back", prob["packet_id"], None,
                  "probation_claim_not_reproduced", tokens)
    else:
        state.bump("probation_pass")
        state.log(task, "probation_passed", prob["packet_id"], None, "")


def _is_effective(state: ArmState, packet: Dict[str, Any]) -> bool:
    return state.truth_of(packet).family == "effective"


def _bump_route(state: ArmState, packet: Dict[str, Any], route: str, task: int) -> None:
    """Count a fast-path route decision; truth is read for accounting only (§8.3)."""
    effective = _is_effective(state, packet)
    harmful = harmful_of(state.stream, packet)
    if route == ROUTE_FAST_ACCEPT:
        state.bump("fast_accept")
        state.log(task, ROUTE_FAST_ACCEPT, packet["id"], ROUTE_FAST_ACCEPT, "", 0)
        if not effective:
            state.bump("false_fast_path")
        if harmful:
            state.bump("harmful_fast_accept")
    elif route == ROUTE_FAST_REJECT:
        state.bump("fast_reject")
        if effective:
            state.bump("false_fast_path")


def _gate(state: ArmState, packet: Dict[str, Any], task: int) -> Dict[str, Any]:
    """One full-gate attempt through the unchanged host gate (§5.1, §7.2)."""
    host = state.host
    host.case = Case(state.stream.run_seed, task)
    n_log = len(host.log)
    reply = host.handle_message(deepcopy(packet))
    rec = host.log[-1] if len(host.log) > n_log else None
    tokens = 0
    if rec is not None:
        tokens = rec.absorb_tokens + rec.isolation_tokens
        if rec.rollback_triggered:
            tokens += sum(e.tokens for e in run_window(host.policy, host.case, "isolation"))
    state.gate_tokens += tokens
    reason = reply.get("reason", "") or ""
    absorbed = bool(rec is not None and rec.status == "absorbed")
    rolled_back = bool(rec is not None and rec.rollback_triggered)
    if absorbed:
        _register_change(state, packet, task, ROUTE_FULL_GATE)
        if harmful_of(state.stream, packet):
            state.bump("harmful_gate_absorbed")
    if rolled_back:
        state.bump("gate_rollback")
    if "token budget exceeded" in reason:
        state.bump("budget_rejections")
    event = "gate_absorbed" if absorbed else ("gate_rolled_back" if rolled_back else "gate_rejected")
    state.log(task, event, packet["id"], ROUTE_FULL_GATE, reason, tokens)
    return {"status": reply.get("status"), "reason": reason, "absorbed": absorbed, "tokens": tokens}


def _absorb_block(state: ArmState, packet: Dict[str, Any]) -> Tuple[str, int, Dict[str, Any]]:
    """Wrapped content, its absorb block tokens, and the parameters derived from it."""
    block = wrap_untrusted(packet)
    absorb = approx_tokens(block)
    params, _evidence = state.host.model.derive(block)
    return block, absorb, params


def _candidate_policy(current: Policy, params: Dict[str, Any], absorb: int) -> Policy:
    """The policy a fast accept would install, with its resident block (§7.4)."""
    candidate = policy_from_params(current, params)
    candidate.context_tokens = current.context_tokens + absorb
    candidate.param_tokens = current.param_tokens + approx_tokens(params)
    return candidate


def _fast_reject(state: ArmState, packet: Dict[str, Any], task: int) -> None:
    """§6.3: no isolation, no content read; the claim read is already charged."""
    state.log(task, ROUTE_FAST_REJECT, packet["id"], ROUTE_FAST_REJECT,
              "repro_claim_below_reference", 0)


def _blind_install(state: ArmState, packet: Dict[str, Any], task: int) -> Dict[str, Any]:
    """D1 fast accept (§6.5): install at once, absorb block only, no checks."""
    host = state.host
    block, absorb, params = _absorb_block(state, packet)
    state.repro_tokens += absorb
    if not params:
        state.log(task, "fast_accept_rejected", packet["id"], ROUTE_FAST_ACCEPT,
                  "no recovery parameters found in packet content", absorb)
        return {"absorbed": False}
    candidate = _candidate_policy(host.policy, params, absorb)
    host.policy = candidate
    host.prompt_blocks.append(block)
    _register_change(state, packet, task, ROUTE_FAST_ACCEPT)
    state.log(task, "fast_accept_installed", packet["id"], ROUTE_FAST_ACCEPT, "", absorb)
    return {"absorbed": True}


def _guarded_fast_accept(state: ArmState, packet: Dict[str, Any], task: int,
                         reference: float, value: Optional[float]) -> Dict[str, Any]:
    """D2/V2 fast accept with the §6.4 spot-check, escalation and probation."""
    host = state.host
    family = state.truth_of(packet).family
    block, absorb, params = _absorb_block(state, packet)
    state.repro_tokens += absorb
    snapshot = _snapshot(host, state.current_harmful)
    successes = 0
    spot_tokens = 0
    candidate: Optional[Policy] = None
    if params:
        candidate = _candidate_policy(host.policy, params, absorb)
        case = Case(state.stream.run_seed, task)
        episodes = [run_episode(candidate, case, SPOT_CHECK_WINDOW, i) for i in range(SPOT_CHECK_N)]
        spot_tokens = sum(e.tokens for e in episodes)
        state.repro_tokens += spot_tokens
        successes = sum(1 for e in episodes if e.success)
    within_budget = bool(params) and absorb + spot_tokens <= TOTAL_TOKEN_BUDGET
    if params and not within_budget:
        state.bump("budget_rejections")
    if params and successes == SPOT_CHECK_N and within_budget:
        host.policy = candidate
        host.prompt_blocks.append(block)
        _register_change(state, packet, task, ROUTE_FAST_ACCEPT)
        _open_probation(state, packet, task, snapshot, reference, value)
        state.bump("spot_check_pass")
        state.bump(f"spot_check_pass_{family}")
        if harmful_of(state.stream, packet):
            state.bump("harmful_spot_check_pass")
        state.log(task, "spot_check_pass", packet["id"], ROUTE_FAST_ACCEPT, "", absorb + spot_tokens)
        return {"absorbed": True}
    reason = "spot_check_failed" if params else "no_recovery_parameters"
    if params and not within_budget:
        reason = "spot_check_budget_exceeded"
    source = source_of(packet)
    if source is not None:
        state.flags.add(source)
    state.bump("spot_check_fail")
    state.bump(f"spot_check_fail_{family}")
    state.bump("escalation")
    state.log(task, "spot_check_fail", packet["id"], ROUTE_FAST_ACCEPT, reason, absorb + spot_tokens)
    state.log(task, "escalation", packet["id"], ROUTE_FULL_GATE, reason, 0)
    out = _gate(state, packet, task)
    return {"absorbed": out["absorbed"], "escalated": True}


def _handle_repro_candidate(state: ArmState, packet: Dict[str, Any], task: int) -> None:
    """D1 (§6.5) and D2 (§6.4): claim read, eligibility, reference, route."""
    guard = state.label == "D2"
    state.repro_tokens += claim_read_tokens(packet)
    reference = reference_of(state.since_change)
    route, code, value = route_for_packet(packet, reference, frozenset(state.flags), guard=guard)
    if route == ROUTE_FULL_GATE:
        state.log(task, "ineligible" if code else "full_gate", packet["id"],
                  ROUTE_FULL_GATE, code or "", 0)
        _gate(state, packet, task)
        return
    _bump_route(state, packet, route, task)
    if route == ROUTE_FAST_REJECT:
        _fast_reject(state, packet, task)
    elif guard:
        _guarded_fast_accept(state, packet, task, reference, value)
    else:
        _blind_install(state, packet, task)


def _handle_v2_candidate(state: ArmState, packet: Dict[str, Any], task: int) -> None:
    """V2 (§7.3): routes fixed by the plan; the claim read is still charged."""
    state.repro_tokens += claim_read_tokens(packet)
    position = task - FIRST_ARRIVAL
    if position in state.v2_reject:
        _bump_route(state, packet, ROUTE_FAST_REJECT, task)
        _fast_reject(state, packet, task)
        return
    if position in state.v2_accept:
        _bump_route(state, packet, ROUTE_FAST_ACCEPT, task)
        reference = reference_of(state.since_change)
        if reference is None:
            reference = recent_mean(state.episodes, PROBATION_EPISODES)
        if reference is None:  # pragma: no cover - unreachable at t >= 11
            reference = 0.0
        _guarded_fast_accept(state, packet, task, reference, claimed_value(packet))
        return
    state.log(task, "full_gate", packet["id"], ROUTE_FULL_GATE, "v2_plan", 0)
    _gate(state, packet, task)


def _handle_candidate(state: ArmState, packet: Dict[str, Any], task: int) -> None:
    """Step 2 of §7: the task's candidate, if any."""
    label = state.label
    if label == "C":
        _gate(state, packet, task)
    elif label == "V1":
        host = state.host
        host.case = Case(state.stream.run_seed, task)
        n_log = len(host.log)
        host.absorb(deepcopy(packet))
        rec = host.log[-1] if len(host.log) > n_log else None
        tokens = rec.absorb_tokens if rec is not None else 0
        state.gate_tokens += tokens
        state.log(task, "stub_rejected", packet["id"], ROUTE_FULL_GATE,
                  (rec.reason if rec is not None else ""), tokens)
    elif label in ("D1", "D2"):
        _handle_repro_candidate(state, packet, task)
    elif label == "V2":
        _handle_v2_candidate(state, packet, task)
    elif label != "A":  # pragma: no cover - guarded by ARMS
        raise ValueError(label)


def _run_task(state: ArmState, task: int) -> None:
    """Step 3 of §7: the task's 6 episodes on the installed policy."""
    policy = state.host.policy
    episodes = state.cache.task(policy, task)
    successes = [ok for ok, _ in episodes]
    tokens = sum(calls * CALL_TOKENS + policy.context_tokens for _, calls in episodes)
    state.task_successes.append(successes)
    state.task_tokens.append(tokens)
    state.episodes.extend(successes)
    state.since_change.extend(successes)
    if state.current_harmful:
        state.harmful_tasks += 1
    if task == PLATEAU[0]:
        state.persistent_harm = state.current_harmful


def run_arm(label: str, seed: int, run: int, stream: Stream,
            cache: Optional[EvalCache] = None, n_tasks: int = TASKS,
            n_fast_reject: int = 0, n_fast_accept: int = 0) -> ArmState:
    """Work one arm through the task sequence of one run (§7)."""
    if label not in ARMS:
        raise ValueError(f"unknown arm {label!r}")
    cache = cache or EvalCache(stream.run_seed)
    state = new_state(label, seed, run, stream, cache)
    if label == "V2":
        state.v2_reject, state.v2_accept = v2_positions(seed, run, n_fast_reject, n_fast_accept)
    last_arrival = FIRST_ARRIVAL + N_CANDIDATES - 1
    for task in range(1, n_tasks + 1):
        if state.probation is not None:
            _check_probation(state, task)
        if FIRST_ARRIVAL <= task <= last_arrival and task - FIRST_ARRIVAL < len(stream.packets):
            _handle_candidate(state, stream.packets[task - FIRST_ARRIVAL], task)
        _run_task(state, task)
    return state


def _mean_sr(successes: Sequence[bool]) -> float:
    return sum(1 for s in successes if s) / len(successes) if successes else 0.0


def endpoints(state: ArmState) -> Dict[str, Any]:
    """Per-arm, per-run endpoints of §8; JSON-serialisable and deterministic."""
    task_sr = [_mean_sr(episodes) for episodes in state.task_successes]
    lo, hi = PLATEAU
    plateau = task_sr[lo - 1:hi]
    gate_tokens = state.gate_tokens
    repro_tokens = state.repro_tokens
    task_tokens = sum(state.task_tokens)
    verification = gate_tokens + repro_tokens
    fr, fa = state.count("fast_reject"), state.count("fast_accept")
    installs = [e for e in state.events if e["event"] in
                ("fast_accept_installed", "spot_check_pass", "gate_absorbed")]
    rejects = [e for e in state.events if e["event"] == ROUTE_FAST_REJECT]
    probation_rollbacks = [e for e in state.events if e["event"] == "probation_rolled_back"]
    traceable = installs + rejects + probation_rollbacks
    attributed = sum(1 for e in traceable if e["packet_id"])
    gate_events = [e for e in state.events
                   if e["event"] in ("gate_absorbed", "gate_rejected", "gate_rolled_back")]
    return {
        "arm": state.label,
        "name": ARMS[state.label],
        "seed": state.seed,
        "run": state.run,
        "k_e": state.stream.k_e,
        "k_s": state.stream.k_s,
        "plateau_SR": (sum(plateau) / len(plateau)) if plateau else 0.0,
        "full_sequence_SR": _mean_sr(state.episodes),
        "task_tokens": task_tokens,
        "gate_tokens": gate_tokens,
        "repro_overhead_tokens": repro_tokens,
        "verification_tokens": verification,
        "total_tokens": task_tokens + verification,
        "resident_context_tokens_T100": state.host.policy.context_tokens,
        "harmful_absorbed": state.harmful_tasks > 0,
        "harmful_installations": state.count("harmful_installs"),
        "persistent_harm": state.persistent_harm,
        "tasks_on_harmful_policy": state.harmful_tasks,
        "harmful_fast_accepted": state.count("harmful_fast_accept"),
        "harmful_spot_check_passed": state.count("harmful_spot_check_pass"),
        "harmful_gate_absorbed": state.count("harmful_gate_absorbed"),
        "harmful_rolled_back": state.count("harmful_rollback"),
        "n_fast_reject": fr,
        "n_fast_accept": fa,
        "fast_path_rate": (fr + fa) / N_CANDIDATES,
        "false_fast_path": state.count("false_fast_path"),
        "spot_check_pass": {f: state.count(f"spot_check_pass_{f}") for f in FAMILY_LABELS},
        "spot_check_fail": {f: state.count(f"spot_check_fail_{f}") for f in FAMILY_LABELS},
        "escalations": state.count("escalation"),
        "probation_rollbacks_true": state.count("probation_rollback_true"),
        "probation_rollbacks_false": state.count("probation_rollback_false"),
        "probation_passed": state.count("probation_pass"),
        "probation_superseded": state.count("probation_superseded"),
        "budget_rejections": state.count("budget_rejections"),
        "rollbacks": {
            "gate": state.count("gate_rollback"),
            "probation": state.count("probation_rollback"),
            "total": state.count("gate_rollback") + state.count("probation_rollback"),
        },
        "policy_changes": state.count("policy_changes"),
        "gate_attempts": len(gate_events),
        "attributed": {
            "policy_changes": sum(1 for e in installs if e["packet_id"]),
            "fast_rejects": sum(1 for e in rejects if e["packet_id"]),
            "probation_rollbacks": sum(1 for e in probation_rollbacks if e["packet_id"]),
            "events": attributed,
            "rate": (attributed / len(traceable)) if traceable else 1.0,
        },
        "flagged_sources": sorted(state.flags),
        "final_policy": state.host.policy.label(),
    }


def run_endpoints(seed: int, run: int, arms: Sequence[str] = ARM_ORDER,
                  n_tasks: int = TASKS) -> Dict[str, Dict[str, Any]]:
    """All requested arms of one (seed, run), keyed by arm, in ``ARM_ORDER``."""
    if "V2" in arms and "D2" not in arms:
        raise ValueError("V2 needs D2's per-run fast-path counts")
    stream = build_stream(seed, run)
    cache = EvalCache(stream.run_seed)
    states: Dict[str, ArmState] = {}
    for label in [a for a in arms if a != "V2"]:
        states[label] = run_arm(label, seed, run, stream, cache, n_tasks=n_tasks)
    if "V2" in arms:
        d2 = states["D2"]
        states["V2"] = run_arm(
            "V2", seed, run, stream, cache, n_tasks=n_tasks,
            n_fast_reject=d2.count("fast_reject"), n_fast_accept=d2.count("fast_accept"),
        )
    return {label: endpoints(states[label]) for label in ARM_ORDER if label in states}


def simulate_run(seed: int, run: int, arms: Sequence[str] = ARM_ORDER,
                 n_tasks: int = TASKS) -> Dict[str, Any]:
    """The per-run record: endpoints per arm plus run metadata."""
    return {"seed": seed, "run": run, "arms": run_endpoints(seed, run, arms, n_tasks)}


__all__ = [
    "ARMS",
    "ARM_ORDER",
    "EPISODES_PER_TASK",
    "PROBATION_TASKS",
    "ROLLBACK_WINDOW",
    "ROUTE_FAST_ACCEPT",
    "ROUTE_FAST_REJECT",
    "ROUTE_FULL_GATE",
    "SPOT_CHECK_WINDOW",
    "ArmState",
    "EvalCache",
    "StubStrategyModel",
    "claimed_value",
    "endpoints",
    "harmful_of",
    "probation_threshold",
    "recent_mean",
    "reference_of",
    "route_for_packet",
    "route_of",
    "run_arm",
    "run_endpoints",
    "simulate_run",
    "v2_positions",
]
