"""Environment, arms and one run of the expiry experiment (PREREG_EXPIRY §4-§7).

One run = one (seed, run index): one version timeline, one peer feed, one task
sequence and one set of gate draws, shared by every arm (paired design, §5).
The host, the gate, the strategy models, ``Case``, ``approx_tokens`` and
``stable_int`` are those of ``experiments/timeout_recovery/host.py``, reused
unchanged. The v2 lease rule of §5.2 is implemented here, in
``run_episode_v2``, which mirrors the loop of ``host.run_episode`` line by line
and adds the lease rule; ``host.py`` is not modified.

No arm sees ``T_shift``: only the timeline generator and the endpoint code
read it (§5.3).
"""

from __future__ import annotations

import math
import random
from copy import deepcopy
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

from experiments.timeout_recovery.host import (
    CALL_TOKENS,
    DEADLINE_MS,
    DELAYED_DRIFT,
    DSR_THRESHOLD,
    ISOLATION_N,
    LATENCY_RANGE,
    MAX_STEPS,
    RECOVERY_MS,
    STALE_ARRIVAL_P,
    STORM_STEP,
    THROTTLE_AFTER_IMMEDIATE,
    THROTTLE_MS,
    TIMEOUT_MS,
    TOTAL_TOKEN_BUDGET,
    WINDOW_EPISODES,
    Case,
    Episode,
    GateRecord,
    Host,
    Policy,
    approx_tokens,
    naive_policy,
    policy_from_params,
    run_episode,
    sr,
    stable_int,
)
from experiments.timeout_recovery.packets import PEER_POLICY, SCENARIO

# ---------------------------------------------------------------------------
# Registered constants (§5, §6, §7, §9, §10, §16)
# ---------------------------------------------------------------------------

SEEDS = (42, 7, 2026, 31337)
RUNS = 40
TASKS = 200
SLOT_TASKS = (1, 26, 51, 76, 101, 126, 151, 176)  # every 25 tasks, §5.1
EPISODES_PER_TASK = 6  # 3 immediate + 3 delayed, §5.1
RECOVERY_WINDOW_K = 25  # §8.1
TASKS_TO_RECOVER_CENSOR = 75  # §8.2
DELTA = Fraction(2, 25)  # 0.08, §10
HALF_WIDTH_FLAG = 0.034  # §10
PRIMARY_HOST = "naive_host"  # §4.2
PILOT_SEED = 42
PILOT_RUNS = (100, 101)
PILOT_TASKS = 200  # §13

#: §5.2: a retry whose backoff wait is longer than this is refused.
LEASE_MS = 300

#: §5.3: version timeline.
T_SHIFT_RANGE = (76, 125)  # inclusive
T_BUMP_RANGE = (31, 70)  # inclusive
SILENT_SHIFT_RATE = 0.25  # s
BENIGN_BUMP_P = 0.50  # q_b
VERSION_V1 = "v1"
VERSION_V1_BUMP = "v1.1"
VERSION_V2 = "v2"

#: §5.4: peer feed.
PEER_LAG = 5  # L
PEER_ID = "peer-tool-1"
PEER_SOURCE_REF = f"peer:{PEER_ID}"
P2_POLICY = Policy(backoff=True, base_backoff_ms=150, max_attempts=3, drop_stale_partials=True)

#: §6.1: the non-normative v0.4 ``expiry`` convention.
EXPIRY_CONVENTION = "tool-version/0"
EXPIRY_TOOL = "web_search"

#: §4.1: label -> (arm name, delivery, expiry trigger, strategy model).
ARMS: Dict[str, Tuple[str, Optional[str], Optional[str], str]] = {
    "A": ("no_sharing", None, None, "none"),
    "C": ("expiry_blind", "feed", None, "rule"),
    "D": ("expiry_enforced", "feed", "predicate", "rule"),
    "V1": ("expiry_blind_stub", "feed", None, "stub"),
    "V2": ("expiry_random", "feed", "random", "rule"),
}
ARM_ORDER = tuple(ARMS)
FEED_ARMS = tuple(label for label in ARM_ORDER if ARMS[label][1] is not None)
EXPIRY_ARMS = tuple(label for label in ARM_ORDER if ARMS[label][2] is not None)


def run_seed_of(seed: int, run: int) -> int:
    """§9: the run seed."""
    return stable_int("expiry", seed, run)


def start_policy() -> Policy:
    """§4.2: every arm starts on ``naive_host``'s start policy."""
    return naive_policy()


class StubStrategyModel:
    """V1 (§4.1, §11.1): ``derive()`` returns ``{}``; nothing usable reaches the policy."""

    name = "stub-empty-derive"

    def __init__(self) -> None:
        self.calls = 0

    def derive(self, block: str) -> Tuple[Dict[str, Any], List[str]]:
        self.calls += 1
        return {}, []


# ---------------------------------------------------------------------------
# Regime v2: the lease rule (§5.2)
# ---------------------------------------------------------------------------


def run_episode_v2(policy: Policy, case: Case, window: str, episode: int) -> Episode:
    """``host.run_episode`` plus the v2 lease rule of §5.2.

    At an attempt whose backoff wait ``w`` satisfies ``w > LEASE_MS`` the three
    uniforms are drawn as usual, the wait is added to the clock, one tool call
    is counted, the call fails at once with event ``session_expired``,
    ``THROTTLE_MS`` is added, ``pending_stale`` is unchanged and the loop
    continues to the next attempt. Attempts with ``w <= LEASE_MS`` (including
    every immediate retry, ``w = 0``) are simulated exactly as in v1.
    """
    rng = case.draws(window, episode)
    base_p = case.base_p + (DELAYED_DRIFT if window == "delayed" else 0.0)
    t = 0
    calls = 0
    consecutive_immediate = 0
    pending_stale = False
    steps: List[Dict[str, Any]] = []
    reason = "max_attempts"
    success = False
    attempt = 0
    while attempt < MAX_STEPS:
        u_timeout, u_stale, u_latency = rng.random(), rng.random(), rng.random()
        if policy.max_attempts is not None and attempt >= policy.max_attempts:
            reason = "max_attempts"
            break
        wait = 0
        if attempt > 0 and policy.backoff:
            wait = policy.base_backoff_ms * (2 ** (attempt - 1))
            consecutive_immediate = 0
        elif attempt > 0:
            consecutive_immediate += 1
        if t + wait >= DEADLINE_MS:
            reason = "deadline"
            break
        t += wait
        calls += 1
        attempt += 1
        if wait > LEASE_MS:
            # The v2 lease rule: the session issued for this request is gone.
            t += THROTTLE_MS
            steps.append({"wait_ms": wait, "event": "session_expired"})
            continue
        if consecutive_immediate >= THROTTLE_AFTER_IMMEDIATE:
            t += THROTTLE_MS
            steps.append({"wait_ms": wait, "event": "throttled"})
            continue
        discarded = False
        if pending_stale and u_stale < STALE_ARRIVAL_P:
            pending_stale = False
            if not policy.drop_stale_partials:
                steps.append({"wait_ms": wait, "event": "stale_accepted"})
                reason = "stale_result_accepted"
                break
            steps.append({"wait_ms": wait, "event": "stale_discarded"})
            discarded = True
        waited = wait
        if discarded:
            wait = 0  # the real call follows in the same attempt; log the wait once
        if waited > 0:
            p = base_p * math.exp(-waited / RECOVERY_MS)
        else:
            p = min(0.98, base_p + STORM_STEP * consecutive_immediate)
        if u_timeout < p:
            t += TIMEOUT_MS
            pending_stale = True
            steps.append({"wait_ms": wait, "event": "timeout"})
            continue
        latency = int(LATENCY_RANGE[0] + u_latency * (LATENCY_RANGE[1] - LATENCY_RANGE[0]))
        if t + latency > DEADLINE_MS:
            t += latency
            steps.append({"wait_ms": wait, "event": "ok"})
            reason = "deadline"
            break
        t += latency
        steps.append({"wait_ms": wait, "event": "ok"})
        success = True
        reason = "ok"
        break
    else:
        reason = "loop_guard"
    if not success and reason == "max_attempts" and t >= DEADLINE_MS:
        reason = "deadline"
    tokens = calls * CALL_TOKENS + policy.context_tokens
    tokens_b = calls * CALL_TOKENS + policy.param_tokens
    return Episode(window, episode, success, reason, calls, tokens, t, steps, tokens_b)


def run_episode_in(regime: str, policy: Policy, case: Case, window: str, episode: int) -> Episode:
    """One episode in the regime in force at that task (§5.2)."""
    if regime == "v2":
        return run_episode_v2(policy, case, window, episode)
    return run_episode(policy, case, window, episode)


def run_window_in(regime: str, policy: Policy, case: Case, window: str) -> List[Episode]:
    """``host.run_window`` in the regime in force at that task."""
    return [run_episode_in(regime, policy, case, window, i) for i in range(WINDOW_EPISODES[window])]


# ---------------------------------------------------------------------------
# Version timeline (§5.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Timeline:
    """One run's version timeline and the peer feed it implies (§5.3, §5.4)."""

    t_shift: int
    detected: bool
    bump: bool
    t_bump: Optional[int]

    def version_at(self, t: int) -> str:
        """The version string every arm observes at the start of task ``t``."""
        if t >= self.t_shift and self.detected:
            return VERSION_V2
        if self.bump and self.t_bump is not None and t >= self.t_bump:
            return VERSION_V1_BUMP
        return VERSION_V1

    def regime_at(self, t: int) -> str:
        """The tool regime in force at task ``t`` (v1 before the shift, v2 after)."""
        return "v2" if t >= self.t_shift else "v1"

    @property
    def t_p2(self) -> int:
        """Publication task of P2 (§5.4): ``T_shift + L``, also after a silent shift."""
        return self.t_shift + PEER_LAG

    @property
    def t_p1b(self) -> Optional[int]:
        """Publication task of P1' (§5.4), only with a benign bump."""
        return None if not self.bump or self.t_bump is None else self.t_bump + PEER_LAG


def draw_timeline(seed: int, run: int) -> Timeline:
    """§5.3: ``T_shift`` then the detection indicator, then the bump indicator and ``T_b``."""
    shift_rng = random.Random(stable_int("expiry-shift", seed, run))
    t_shift = shift_rng.randint(*T_SHIFT_RANGE)
    detected = shift_rng.random() >= SILENT_SHIFT_RATE
    benign_rng = random.Random(stable_int("expiry-benign", seed, run))
    bump = benign_rng.random() < BENIGN_BUMP_P
    t_bump = benign_rng.randint(*T_BUMP_RANGE) if bump else None
    return Timeline(t_shift=t_shift, detected=detected, bump=bump, t_bump=t_bump)


# ---------------------------------------------------------------------------
# The ``expiry`` convention (§6.1)
# ---------------------------------------------------------------------------


def expiry_object(tool_version_neq: str) -> Dict[str, Any]:
    """The §6.1 object as the peer publishes it."""
    return {"convention": EXPIRY_CONVENTION, "tool": EXPIRY_TOOL, "tool_version_neq": tool_version_neq}


def expiry_evaluable(expiry: Any) -> bool:
    """§6.1: is the predicate evaluable at all?"""
    if not isinstance(expiry, dict):
        return False
    if expiry.get("convention") != EXPIRY_CONVENTION:
        return False
    if expiry.get("tool") != EXPIRY_TOOL:
        return False
    return isinstance(expiry.get("tool_version_neq"), str)


def expiry_expired(expiry: Any, observed_version: str) -> Optional[bool]:
    """§6.1: ``True`` expired, ``False`` valid, ``None`` not evaluable (never fires)."""
    if not expiry_evaluable(expiry):
        return None
    return observed_version != expiry["tool_version_neq"]


# ---------------------------------------------------------------------------
# Peer feed (§5.4)
# ---------------------------------------------------------------------------


def _packet(pid: str, content: Dict[str, Any], tool_version_neq: str) -> Dict[str, Any]:
    return {
        "id": pid,
        "version": "0.4",
        "type": "failure_mode",
        "content": content,
        "absorption": {"method": "prompt_injection", "target": "tool_retry_policy"},
        "provenance": {"source_ref": PEER_SOURCE_REF},
        "expiry": expiry_object(tool_version_neq),
    }


def _policy_content(policy: Policy, signal: str) -> Dict[str, Any]:
    return {
        "trigger": SCENARIO,
        "recovery_policy": {
            "backoff": "exponential" if policy.backoff else "none",
            "base_backoff_ms": policy.base_backoff_ms,
            "max_attempts": policy.max_attempts,
            "drop_stale_partials": policy.drop_stale_partials,
        },
        "compressed_signal": signal,
    }


def _policy_signal(policy: Policy) -> str:
    return (
        f"exponential backoff from {policy.base_backoff_ms} ms, at most "
        f"{policy.max_attempts} attempts, drop late partial results"
    )


def feed_packets(run_seed: int, timeline: Timeline) -> List[Dict[str, Any]]:
    """The peer feed of one run, in publication order (§5.4)."""
    out = [_packet(f"pkt-exp-{run_seed}-p1", _policy_content(PEER_POLICY, _policy_signal(PEER_POLICY)), VERSION_V1)]
    if timeline.bump:
        out.append(_packet(
            f"pkt-exp-{run_seed}-p1b", _policy_content(PEER_POLICY, _policy_signal(PEER_POLICY)), VERSION_V1_BUMP
        ))
    # §5.4: P2's ``tool_version_neq`` is the version observed at ``T_shift + 5``:
    # "v2" if the shift is detected, "v1" or "v1.1" if it is silent. The content
    # needed to recover is in the feed for every arm; detection only sets the
    # predicate's verdict.
    out.append(_packet(
        f"pkt-exp-{run_seed}-p2",
        _policy_content(P2_POLICY, _policy_signal(P2_POLICY)),
        timeline.version_at(timeline.t_p2),
    ))
    return out


def feed_publication_tasks(timeline: Timeline) -> List[int]:
    """Publication task of each feed packet, in the same order as ``feed_packets``."""
    out = [1]
    if timeline.bump:
        out.append(timeline.t_p1b or 1)
    out.append(timeline.t_p2)
    return out


def feed_at(packets: Sequence[Dict[str, Any]], tasks: Sequence[int], t: int) -> List[Dict[str, Any]]:
    """Packets published at or before task ``t``, in publication order."""
    return [p for p, pub in zip(packets, tasks) if pub <= t]


def feed_read_tokens(packets: Sequence[Dict[str, Any]]) -> int:
    """§7.2: ``approx_tokens`` of the envelopes (every field except ``content``)."""
    return sum(approx_tokens({k: v for k, v in p.items() if k != "content"}) for p in packets)


def expiry_check_tokens(packet: Dict[str, Any], observed_version: str) -> int:
    """§7.2: the per-task expiry check."""
    return approx_tokens({"expiry": packet.get("expiry"), "tool_version": observed_version})


# ---------------------------------------------------------------------------
# V2 trigger (§7.3)
# ---------------------------------------------------------------------------


def v2_trigger_tasks(seed: int, run: int, k: int, n_tasks: int = TASKS) -> List[int]:
    """``k`` distinct tasks drawn uniformly from T2-T200, stream ``("expiry-v2", seed, run)``."""
    if k <= 0:
        return []
    rng = random.Random(stable_int("expiry-v2", seed, run))
    return sorted(rng.sample(range(2, n_tasks + 1), min(k, n_tasks - 1)))


# ---------------------------------------------------------------------------
# One arm in one run (§7)
# ---------------------------------------------------------------------------


@dataclass
class ArmRun:
    """Everything one arm did in one run."""

    arm: str
    seed: int
    run: int
    run_seed: int
    t_shift: int
    detected: bool
    bump: bool
    successes: List[int] = field(default_factory=list)  # successes per task, out of 6
    task_tokens: List[int] = field(default_factory=list)
    tool_calls: List[int] = field(default_factory=list)  # task episodes only
    gate_tool_calls: List[int] = field(default_factory=list)  # isolation + rollback replay
    expiry_overhead: List[int] = field(default_factory=list)
    policy_source: List[Optional[str]] = field(default_factory=list)  # packet id behind the policy
    policy_pub: List[Optional[int]] = field(default_factory=list)  # its publication task
    policy_label: List[str] = field(default_factory=list)  # the policy in force for the task
    policy_origin: List[str] = field(default_factory=list)  # "host_start" or "packet"
    observed_version: List[str] = field(default_factory=list)  # §5.3, observed at task start
    regime: List[str] = field(default_factory=list)  # "v1" or "v2" (§5.2)
    expiry_events: List[int] = field(default_factory=list)  # tasks with an expiry event
    expiry_unevaluable: int = 0
    content_policy_rejections: int = 0
    gate_attempts: List[Dict[str, Any]] = field(default_factory=list)
    reverts: List[Dict[str, Any]] = field(default_factory=list)
    final_policy: str = ""

    def task_sr(self, t: int) -> float:
        return self.successes[t - 1] / EPISODES_PER_TASK

    def gate_attempts_at(self, t: int) -> List[Dict[str, Any]]:
        """Gate attempts made at task ``t`` (§5.1: at most one per arm per task)."""
        return [g for g in self.gate_attempts if g["task"] == t]

    def task_records(self, n_tasks: Optional[int] = None) -> List[Dict[str, Any]]:
        """One record per task: the §8 inputs for this arm (§5.1, §7).

        Fields: ``task``, ``successes`` (of 6), ``sr``, ``policy_label``,
        ``policy_origin`` (``packet`` / ``host_start``), ``packet_id``,
        ``packet_pub``, ``tool_calls`` (task episodes), ``tokens``,
        ``expiry_overhead``, ``observed_version``, ``regime``, ``expiry_event``,
        ``gate_attempts`` (the compact records of :meth:`gate_attempts_at`).
        """
        n = len(self.successes) if n_tasks is None else min(n_tasks, len(self.successes))
        events = set(self.expiry_events)
        out: List[Dict[str, Any]] = []
        for t in range(1, n + 1):
            out.append({
                "task": t,
                "successes": self.successes[t - 1],
                "sr": self.successes[t - 1] / EPISODES_PER_TASK,
                "policy_label": self.policy_label[t - 1],
                "policy_origin": self.policy_origin[t - 1],
                "packet_id": self.policy_source[t - 1],
                "packet_pub": self.policy_pub[t - 1],
                "tool_calls": self.tool_calls[t - 1],
                "tokens": self.task_tokens[t - 1],
                "expiry_overhead": self.expiry_overhead[t - 1],
                "observed_version": self.observed_version[t - 1],
                "regime": self.regime[t - 1],
                "expiry_event": t in events,
                "gate_attempts": self.gate_attempts_at(t),
            })
        return out


class RegimeHost(Host):
    """``Host`` whose gate episodes run in the regime in force (§5.1).

    ``host.Host.gate`` replays the isolation window with ``host.run_episode``,
    i.e. always in regime v1. §5.1 requires the isolation and rollback-check
    episodes to run in the regime in force at that task, so the gate loop is
    reproduced here with :func:`run_window_in`; everything else (budgets,
    ΔSR rule, regression check, single-step rollback) is ``host.gate``
    unchanged and ``host.py`` is not modified.
    """

    def __init__(self, case: Case, model: Any = None, regime: str = "v1") -> None:
        super().__init__(case, model)
        self.regime = regime

    def gate(
        self,
        source: str,
        params: Dict[str, Any],
        evidence: List[str],
        absorb_tokens: int,
        *,
        context_tokens: int,
        install: Any = None,
        uninstall: Any = None,
    ) -> GateRecord:
        snapshot = deepcopy(self.policy)
        pristine = (deepcopy(self.policy), list(self.prompt_blocks), dict(self.retrieval_store))
        before = run_window_in(self.regime, snapshot, self.case, "isolation")
        candidate = policy_from_params(snapshot, params)
        candidate.context_tokens = snapshot.context_tokens + context_tokens
        candidate.param_tokens = snapshot.param_tokens + approx_tokens(params)
        self.policy = candidate
        if install:
            install()
        after = run_window_in(self.regime, candidate, self.case, "isolation")
        iso_tokens = sum(e.tokens for e in before) + sum(e.tokens for e in after)
        iso_tokens_b = sum(e.tokens_b for e in before) + sum(e.tokens_b for e in after)
        iso_calls = sum(e.tool_calls for e in before) + sum(e.tool_calls for e in after)
        sr_before, sr_after = sr(before), sr(after)
        dsr = round(sr_after - sr_before, 4)
        regressed = any(b.success and not a.success for b, a in zip(before, after))
        spent = absorb_tokens + sum(e.tokens for e in after)
        if spent > TOTAL_TOKEN_BUDGET:
            status, reason = "rejected", (
                f"token budget exceeded: absorption + isolation used {spent} > {TOTAL_TOKEN_BUDGET}; rolled back"
            )
        elif regressed:
            status, reason = "rejected", "an isolation trial that passed before failed after the change; rolled back"
        elif dsr < DSR_THRESHOLD:
            status, reason = "rejected", (
                f"isolation gain {dsr:.3f} below threshold {DSR_THRESHOLD} over {ISOLATION_N} trials; rolled back"
            )
        else:
            status, reason = "absorbed", ""
        rollback_ok: Optional[bool] = None
        if status != "absorbed":
            self.policy = snapshot
            if uninstall:
                uninstall()
            replay = [
                run_episode_in(self.regime, snapshot, self.case, "isolation", e.index) for e in before
            ]
            restored = (self.policy, self.prompt_blocks, self.retrieval_store) == pristine
            rollback_ok = restored and [e.success for e in replay] == [e.success for e in before]
        record = GateRecord(
            source=source, status=status, reason=reason, params=dict(params), evidence=list(evidence),
            policy_before=snapshot.label(), policy_after=candidate.label(), absorb_tokens=absorb_tokens,
            isolation_tokens=iso_tokens, isolation_tool_calls=iso_calls,
            isolation_sr_before=round(sr_before, 4), isolation_sr_after=round(sr_after, 4), isolation_dsr=dsr,
            rollback_triggered=status != "absorbed", rollback_ok=rollback_ok, model_calls=0,
            isolation_tokens_b=iso_tokens_b,
        )
        self.log.append(record)
        return record


def _deliver(host: RegimeHost, packet: Dict[str, Any], regime: str, task: int, source: str) -> Dict[str, Any]:
    """Send one packet through the host gate; return a compact record."""
    n_log = len(host.log)
    policy_before = host.policy.label()
    reply = host.handle_message(deepcopy(packet))
    rec = host.log[-1] if len(host.log) > n_log else None
    gate_calls = 0
    tokens = 0
    if rec is not None:
        tokens = rec.absorb_tokens + rec.isolation_tokens
        gate_calls = rec.isolation_tool_calls
        if rec.rollback_triggered:
            # §5.1: the rollback-check episodes run in the regime in force.
            replay = run_window_in(regime, host.policy, host.case, "rollback_check")
            tokens += sum(e.tokens for e in replay)
            gate_calls += sum(e.tool_calls for e in replay)
    return {
        "task": task,
        "source": source,
        "packet_id": packet["id"],
        "status": reply.get("status"),
        "reason": reply.get("reason", ""),
        "reply_ref": reply.get("packet_ref"),
        "content_policy": str(reply.get("reason", "")).startswith("content_policy:"),
        "rolled_back": bool(rec and rec.rollback_triggered),
        "rollback_ok": rec.rollback_ok if rec else None,
        "policy_changed": host.policy.label() != policy_before,
        "policy_before": policy_before,
        "policy_after": host.policy.label(),
        "tokens": tokens,
        "gate_tool_calls": gate_calls,
        "attributed": bool(
            rec is not None
            and reply.get("status") == "absorbed"
            and reply.get("packet_ref") == packet["id"]
        ),
    }


def _valid_at(packet: Dict[str, Any], version: str) -> bool:
    """§7 steps 4 and 5: the packet's ``expiry`` is valid at the observed version."""
    return expiry_expired(packet.get("expiry"), version) is False


def run_arm(label: str, seed: int, run: int, timeline: Timeline, n_tasks: int = TASKS,
            v2_triggers: Optional[Sequence[int]] = None) -> ArmRun:
    """Work through the task sequence for one arm (§7)."""
    _name, delivery, trigger, model_kind = ARMS[label]
    run_seed = run_seed_of(seed, run)
    packets = feed_packets(run_seed, timeline)
    pubs = feed_publication_tasks(timeline)
    model = StubStrategyModel() if model_kind == "stub" else None
    host = RegimeHost(Case(run_seed, 1), model)
    host.policy = start_policy()
    out = ArmRun(arm=label, seed=seed, run=run, run_seed=run_seed, t_shift=timeline.t_shift,
                 detected=timeline.detected, bump=timeline.bump)
    installed: Optional[Dict[str, Any]] = None  # the packet behind the installed policy
    installed_pub: Optional[int] = None
    expired_state = False
    tried_in_state: set = set()
    triggers = set(v2_triggers or ())
    for t in range(1, n_tasks + 1):
        version = timeline.version_at(t)
        regime = timeline.regime_at(t)
        host.case = Case(run_seed, t)
        host.regime = regime  # §5.1: gate episodes run in the regime in force
        feed = feed_at(packets, pubs, t)
        overhead = 0
        gate_this_task = False
        gate_calls_this_task = 0
        # Step 2: expiry trigger.
        event = False
        if trigger == "predicate" and installed is not None:
            verdict = expiry_expired(installed.get("expiry"), version)
            if verdict is None:
                out.expiry_unevaluable += 1
            event = bool(verdict)
        elif trigger == "random":
            event = t in triggers
        # Step 3: expiry event.
        if event:
            out.expiry_events.append(t)
            if installed is not None:
                out.reverts.append({
                    "task": t, "packet_id": installed["id"], "result": "expired",
                    "policy_before": host.policy.label(),
                })
                host.policy = start_policy()
                installed = None
                installed_pub = None
            expired_state = True
            tried_in_state = set()
        # Step 4: expired-state fetch (D, V2, while in the expired state).
        if delivery is not None and trigger is not None and expired_state:
            candidate = None
            for pkt in reversed(feed):
                if pkt["id"] in tried_in_state:
                    continue
                if not _valid_at(pkt, version):
                    continue
                candidate = pkt
                break
            if candidate is not None:
                tried_in_state.add(candidate["id"])
                overhead += feed_read_tokens(feed)
                res = _deliver(host, candidate, regime, t, "expired_fetch")
                gate_this_task = True
                out.gate_attempts.append(res)
                gate_calls_this_task += res["gate_tool_calls"]
                if res["content_policy"]:
                    out.content_policy_rejections += 1
                if res["status"] == "absorbed":
                    installed = candidate
                    installed_pub = pubs[packets.index(candidate)]
                    expired_state = False
                    tried_in_state = set()
        # Step 5: scheduled slot (§7.2: the feed read is charged at every slot;
        # §7 / §16.10: no gate if step 4 already ran one this task).
        if delivery is not None and t in SLOT_TASKS:
            overhead += feed_read_tokens(feed)
            if not gate_this_task:
                installed_content = None if installed is None else installed.get("content")
                candidate = None
                for pkt in reversed(feed):
                    if trigger is not None and not _valid_at(pkt, version):
                        continue
                    if pkt.get("content") == installed_content:
                        continue
                    candidate = pkt
                    break
                if candidate is not None:
                    res = _deliver(host, candidate, regime, t, "slot")
                    out.gate_attempts.append(res)
                    gate_calls_this_task += res["gate_tool_calls"]
                    if res["content_policy"]:
                        out.content_policy_rejections += 1
                    if res["status"] == "absorbed":
                        installed = candidate
                        installed_pub = pubs[packets.index(candidate)]
                        expired_state = False
                        tried_in_state = set()
        # §7.2: the per-task expiry check for D and V2.
        if trigger is not None and installed is not None:
            overhead += expiry_check_tokens(installed, version)
        # Step 6: the task's 6 episodes in the current regime.
        eps = run_window_in(regime, host.policy, host.case, "immediate") + run_window_in(
            regime, host.policy, host.case, "delayed"
        )
        out.successes.append(sum(1 for e in eps if e.success))
        out.tool_calls.append(sum(e.tool_calls for e in eps))
        out.task_tokens.append(sum(e.tokens for e in eps))
        out.expiry_overhead.append(overhead)
        out.gate_tool_calls.append(gate_calls_this_task)
        out.policy_source.append(None if installed is None else installed["id"])
        out.policy_pub.append(installed_pub)
        out.policy_label.append(host.policy.label())
        out.policy_origin.append("packet" if installed is not None else "host_start")
        out.observed_version.append(version)
        out.regime.append(regime)
    out.final_policy = host.policy.label()
    return out


def simulate_run(seed: int, run: int, n_tasks: int = TASKS, arms: Sequence[str] = ARM_ORDER) -> Dict[str, Any]:
    """All arms of one (seed, run), paired on the timeline, the feed and the task draws.

    Returns the run-level metadata of §5.3/§7.3 (``t_shift``, ``detected``,
    ``silent``, ``bump``, ``t_bump``, ``t_p2``, ``t_p1b``, ``v2_k``,
    ``v2_triggers``), the :class:`Timeline` itself, and ``arms``: label ->
    :class:`ArmRun` (per-task records; see :meth:`ArmRun.task_records`).
    """
    timeline = draw_timeline(seed, run)
    base = {label: run_arm(label, seed, run, timeline, n_tasks) for label in arms if label != "V2"}
    out: Dict[str, Any] = {
        "seed": seed,
        "run": run,
        "run_seed": run_seed_of(seed, run),
        "n_tasks": n_tasks,
        "t_shift": timeline.t_shift,
        "detected": timeline.detected,
        "silent": not timeline.detected,
        "bump": timeline.bump,
        "t_bump": timeline.t_bump,
        "t_p1b": timeline.t_p1b,
        "t_p2": timeline.t_p2,
        "timeline": timeline,
        "arms": base,
    }
    if "V2" in arms:
        k = len(base["D"].expiry_events) if "D" in base else 0
        triggers = v2_trigger_tasks(seed, run, k, n_tasks)
        out["v2_k"] = k
        out["v2_triggers"] = triggers
        out["arms"]["V2"] = run_arm("V2", seed, run, timeline, n_tasks, triggers)
    return out


# ---------------------------------------------------------------------------
# Endpoints (§8)
# ---------------------------------------------------------------------------


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def window_range(t_shift: int, n_tasks: int = TASKS) -> Tuple[int, int]:
    """§8.1: ``T_shift … T_shift + 24``, clipped to the sequence."""
    return t_shift, min(t_shift + RECOVERY_WINDOW_K - 1, n_tasks)


def recovery_window_sr(arm: ArmRun, n_tasks: int = TASKS) -> float:
    lo, hi = window_range(arm.t_shift, n_tasks)
    return sum(arm.successes[lo - 1:hi]) / ((hi - lo + 1) * EPISODES_PER_TASK)


def pre_shift_sr(arm: ArmRun) -> float:
    """§8.2: mean task SR over T1 … T_shift - 1."""
    n = arm.t_shift - 1
    return sum(arm.successes[:n]) / (n * EPISODES_PER_TASK) if n > 0 else 0.0


def full_sequence_sr(arm: ArmRun) -> float:
    return sum(arm.successes) / (len(arm.successes) * EPISODES_PER_TASK)


def pre_ref_sr(arm: ArmRun) -> float:
    """§8.2: mean task SR over T26-T75 (always before any shift)."""
    return sum(arm.successes[25:75]) / (50 * EPISODES_PER_TASK)


def tasks_to_recover(arm: ArmRun, n_tasks: int = TASKS) -> Tuple[int, bool]:
    """§8.2: ``t_rec - T_shift``, censored at 75 (value 75, flagged)."""
    for t in range(arm.t_shift, n_tasks + 1):
        pub = arm.policy_pub[t - 1]
        if pub is not None and pub >= arm.t_shift:
            return t - arm.t_shift, False
    return TASKS_TO_RECOVER_CENSOR, True


def stale_policy_uses(arm: ArmRun, n_tasks: int = TASKS) -> Tuple[int, int]:
    """§8.2: tasks >= T_shift on a policy from a packet published before T_shift.

    Returns ``(in the recovery window, over T_shift … T200)``.
    """
    lo, hi = window_range(arm.t_shift, n_tasks)
    window = 0
    total = 0
    for t in range(arm.t_shift, n_tasks + 1):
        pub = arm.policy_pub[t - 1]
        if pub is not None and pub < arm.t_shift:
            total += 1
            if lo <= t <= hi:
                window += 1
    return window, total


def endpoints(arm: ArmRun, n_tasks: int = TASKS) -> Dict[str, Any]:
    """Every endpoint of §8 for one arm in one run."""
    lo, hi = window_range(arm.t_shift, n_tasks)
    ttr, censored = tasks_to_recover(arm, n_tasks)
    stale_window, stale_total = stale_policy_uses(arm, n_tasks)
    changes = [g for g in arm.gate_attempts if g["policy_changed"]]
    attributed = sum(1 for g in changes if g["attributed"])
    reverts = len(arm.reverts)
    attributed_reverts = sum(1 for r in arm.reverts if r["packet_id"] and r["result"] == "expired")
    # §8.2: total tokens include the evaluation episodes, the gate (absorption,
    # isolation, rollback check) and the §7.2 expiry charges.
    gate_tokens = sum(g["tokens"] for g in arm.gate_attempts)
    return {
        "arm": arm.arm,
        "recovery_window_sr": recovery_window_sr(arm, n_tasks),
        "recovery_window_tasks": hi - lo + 1,
        "tasks_to_recover": ttr,
        "tasks_to_recover_censored": censored,
        "stale_policy_uses_window": stale_window,
        "stale_policy_uses_total": stale_total,
        "task_tool_calls_window": sum(arm.tool_calls[lo - 1:hi]),
        "gate_tool_calls_window": sum(arm.gate_tool_calls[lo - 1:hi]),
        "false_positive_expiries": sum(1 for t in arm.expiry_events if t < arm.t_shift),
        "expiry_events": len(arm.expiry_events),
        "expiry_unevaluable": arm.expiry_unevaluable,
        "full_sequence_sr": full_sequence_sr(arm),
        "pre_shift_sr": pre_shift_sr(arm),
        "pre_ref_sr": pre_ref_sr(arm),
        "total_tokens": sum(arm.task_tokens) + sum(arm.expiry_overhead) + gate_tokens,
        "eval_tokens": sum(arm.task_tokens),
        "gate_tokens": gate_tokens,
        "expiry_overhead_tokens": sum(arm.expiry_overhead),
        "policy_changes": len(changes),
        "attributed_changes": attributed,
        "expiry_reverts": reverts,
        "attributed_reverts": attributed_reverts,
        "content_policy_rejections": arm.content_policy_rejections,
        "final_policy": arm.final_policy,
    }


def run_endpoints(run_result: Dict[str, Any], n_tasks: int = TASKS) -> Dict[str, Any]:
    """Endpoints of every arm of one run, plus the arm-A-relative tool-call count."""
    arms = {label: endpoints(arm, n_tasks) for label, arm in run_result["arms"].items()}
    a_calls = arms["A"]["task_tool_calls_window"] if "A" in arms else 0
    for label, ep in arms.items():
        ep["extra_tool_calls"] = ep["task_tool_calls_window"] + ep["gate_tool_calls_window"] - a_calls
    return {
        "seed": run_result["seed"],
        "run": run_result["run"],
        "t_shift": run_result["t_shift"],
        "detected": run_result["detected"],
        "silent": run_result.get("silent", not run_result["detected"]),
        "bump": run_result["bump"],
        "t_bump": run_result.get("t_bump"),
        "v2_k": run_result.get("v2_k"),
        "v2_triggers": run_result.get("v2_triggers"),
        "arms": arms,
    }


__all__ = [
    "ARMS",
    "ARM_ORDER",
    "ArmRun",
    "BENIGN_BUMP_P",
    "DELTA",
    "EPISODES_PER_TASK",
    "EXPIRY_ARMS",
    "EXPIRY_CONVENTION",
    "EXPIRY_TOOL",
    "FEED_ARMS",
    "HALF_WIDTH_FLAG",
    "LEASE_MS",
    "P2_POLICY",
    "PEER_LAG",
    "PEER_POLICY",
    "PEER_SOURCE_REF",
    "PILOT_RUNS",
    "PILOT_SEED",
    "PILOT_TASKS",
    "PRIMARY_HOST",
    "RECOVERY_WINDOW_K",
    "RUNS",
    "RegimeHost",
    "SEEDS",
    "SILENT_SHIFT_RATE",
    "SLOT_TASKS",
    "StubStrategyModel",
    "TASKS",
    "TASKS_TO_RECOVER_CENSOR",
    "T_BUMP_RANGE",
    "T_SHIFT_RANGE",
    "Timeline",
    "VERSION_V1",
    "VERSION_V1_BUMP",
    "VERSION_V2",
    "draw_timeline",
    "endpoints",
    "expiry_check_tokens",
    "expiry_evaluable",
    "expiry_expired",
    "expiry_object",
    "feed_at",
    "feed_packets",
    "feed_publication_tasks",
    "feed_read_tokens",
    "full_sequence_sr",
    "pre_ref_sr",
    "pre_shift_sr",
    "recovery_window_sr",
    "run_arm",
    "run_endpoints",
    "run_episode_in",
    "run_episode_v2",
    "run_seed_of",
    "run_window_in",
    "simulate_run",
    "stale_policy_uses",
    "start_policy",
    "tasks_to_recover",
    "v2_trigger_tasks",
    "window_range",
]
