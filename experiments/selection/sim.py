"""Arms, selection rules and one run of the selection experiment (PREREG_SELECTION §4-§8).

One run = one (seed, run index): one packet pool, one task sequence, one random
order, shared by every arm on both hosts (paired design, §5). The host, the
gate, the strategy model and the tool simulation are those of
``experiments/timeout_recovery/host.py``, unchanged.
"""

from __future__ import annotations

import random
from copy import deepcopy
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ai_oriented_a2a import scan_directives
from ai_oriented_a2a.recovery import parse_recovery_params

from experiments.timeout_recovery.host import (
    CALL_TOKENS,
    Case,
    Host,
    Policy,
    approx_tokens,
    naive_policy,
    run_window,
    stable_int,
)
from experiments.timeout_recovery.packets import working_policy

from .pool import (
    POOL_SIZE,
    PoolPacket,
    build_pool,
    envelope_of,
    random_order,
    shuffle_envelopes,
    source_of,
)

# ---------------------------------------------------------------------------
# Registered constants (§5, §7, §8, §9, §10)
# ---------------------------------------------------------------------------

SEEDS = (42, 7, 2026, 31337)
RUNS = 40
TASKS = 200
SLOT_TASKS = (1, 26, 51, 76)
WINDOW_TASKS = 25
PLATEAU_TASKS = 50  # T151-T200
EPISODES_PER_TASK = 6  # 3 immediate + 3 delayed
DELTA = Fraction(1, 10)
HISTORY_WEIGHT = 3
HOSTS = ("naive_host", "working_host")
PRIMARY_HOST = "naive_host"
PILOT_SEED = 42
PILOT_RUNS = (100, 101)
PILOT_TASKS = 50

#: label -> (arm name, delivery, selection, envelope view) - §4.1
ARMS: Dict[str, Tuple[str, Optional[str], Optional[str], str]] = {
    "A": ("no_sharing", None, None, "none"),
    "B": ("local_file_gated", "file", "random", "none"),
    "C": ("protocol_blind", "protocol", "random", "full"),
    "D1": ("protocol_informed_history", "protocol", "history", "full"),
    "D2": ("protocol_informed_claim", "protocol", "claim", "full"),
    "V1": ("protocol_blind_stub", "stub", "random", "full"),
    "V2": ("protocol_informed_history_shuffled", "protocol", "history", "shuffled"),
}
ARM_ORDER = tuple(ARMS)
ENVELOPE_READING = ("history", "claim")


def run_seed_of(seed: int, run: int) -> int:
    return stable_int("selection", seed, run)


def start_policy(host_name: str) -> Policy:
    if host_name == "naive_host":
        return naive_policy()
    if host_name == "working_host":
        return working_policy()
    raise ValueError(host_name)


class StubStrategyModel:
    """V1 (§11.1): ``derive()`` returns ``{}``; nothing usable reaches the policy."""

    name = "stub-empty-derive"

    def __init__(self) -> None:
        self.calls = 0

    def derive(self, block: str) -> Tuple[Dict[str, Any], List[str]]:
        self.calls += 1
        return {}, []


# ---------------------------------------------------------------------------
# Selection rules (§7)
# ---------------------------------------------------------------------------


def history_score(packet: Dict[str, Any], own: Dict[str, List[int]]) -> Fraction:
    """§7.2: Beta(1, 1) posterior mean of prior outcomes plus 3 x own results."""
    prior = packet.get("prior_outcomes") or {}
    own_a, own_r = own.get(source_of(packet) or "", [0, 0])
    a = int(prior.get("absorbed_count", 0)) + HISTORY_WEIGHT * own_a
    r = int(prior.get("rejected_count", 0)) + HISTORY_WEIGHT * own_r
    return Fraction(a + 1, a + r + 2)


def claim_eligible(packet: Dict[str, Any]) -> bool:
    claim = packet.get("verification_claim") or {}
    repro = claim.get("repro") or {}
    n = repro.get("n")
    return (claim.get("metric") == "recovery_sr" and isinstance(n, int) and not isinstance(n, bool)
            and n >= 30 and isinstance(claim.get("value"), (int, float)))


def select_history(untried: Sequence[int], packets: Sequence[Dict[str, Any]],
                   own: Dict[str, List[int]], tie_rng: random.Random) -> int:
    scores = {i: history_score(packets[i], own) for i in untried}
    best = max(scores.values())
    tied = sorted(i for i, s in scores.items() if s == best)
    return tied[0] if len(tied) == 1 else tie_rng.choice(tied)


def claim_ranking(untried: Sequence[int], packets: Sequence[Dict[str, Any]], tie_rng: random.Random) -> List[int]:
    """§7.3: eligible by value descending (ties by the seeded RNG), then the
    ineligible packets in random order."""
    eligible = sorted(i for i in untried if claim_eligible(packets[i]))
    ineligible = sorted(i for i in untried if not claim_eligible(packets[i]))
    keys = {i: tie_rng.random() for i in eligible}
    eligible.sort(key=lambda i: (-float(packets[i]["verification_claim"]["value"]), keys[i]))
    tie_rng.shuffle(ineligible)
    return eligible + ineligible


def select_claim(untried: Sequence[int], packets: Sequence[Dict[str, Any]], tie_rng: random.Random) -> int:
    return claim_ranking(untried, packets, tie_rng)[0]


# ---------------------------------------------------------------------------
# One arm on one host in one run
# ---------------------------------------------------------------------------


class EvalCache:
    """Paired evaluation episodes depend only on (policy label, task); token
    counts add the policy's resident context. Shared by all arms of a run."""

    def __init__(self, run_seed: int) -> None:
        self.run_seed = run_seed
        self.store: Dict[Tuple[str, int], List[Tuple[bool, int]]] = {}

    def task(self, policy: Policy, t: int) -> Tuple[int, int]:
        key = (policy.label(), t)
        eps = self.store.get(key)
        if eps is None:
            case = Case(self.run_seed, t)
            eps = [(e.success, e.tool_calls) for e in run_window(policy, case, "immediate") + run_window(policy, case, "delayed")]
            self.store[key] = eps
        successes = sum(1 for s, _ in eps if s)
        tokens = sum(c * CALL_TOKENS + policy.context_tokens for _, c in eps)
        return successes, tokens


def _rollback_tokens(host: Host, rec: Any) -> int:
    """Tokens of the post-rollback replay the gate runs (not in GateRecord)."""
    if rec is None or not rec.rollback_triggered:
        return 0
    return sum(e.tokens for e in run_window(host.policy, host.case, "isolation"))


def deliver_protocol(host: Host, packet: Dict[str, Any]) -> Dict[str, Any]:
    n_log = len(host.log)
    policy_before = host.policy.label()
    reply = host.handle_message(deepcopy(packet))
    rec = host.log[-1] if len(host.log) > n_log else None
    tokens = (rec.absorb_tokens + rec.isolation_tokens) if rec else 0
    tokens += _rollback_tokens(host, rec)
    return {
        "status": reply.get("status"),
        "reason": reply.get("reason", ""),
        "reply_ref": reply.get("packet_ref"),
        "has_result": True,
        "rolled_back": bool(rec and rec.rollback_triggered),
        "rollback_ok": rec.rollback_ok if rec else None,
        "policy_changed": host.policy.label() != policy_before,
        "tokens": tokens,
    }


def deliver_file(host: Host, packet: Dict[str, Any]) -> Dict[str, Any]:
    """B: the content as file text through the same gate; no packet id, no
    Absorption Result (as ``plain_file_gated`` in WP3)."""
    content = packet.get("content")
    import json

    text = json.dumps(content, ensure_ascii=False, sort_keys=True)
    read_tokens = approx_tokens(text)
    policy_before = host.policy.label()
    out = {"reply_ref": None, "has_result": False, "rolled_back": False, "rollback_ok": None,
           "policy_changed": False, "tokens": read_tokens}
    hits = scan_directives(content)
    if hits:
        return dict(out, status="rejected", reason=f"content_policy: directive pattern(s) in content: {', '.join(hits)}")
    params, evidence = parse_recovery_params(content)
    if not params:
        return dict(out, status="rejected", reason="no recovery parameters found in file content")
    rec = host.gate("file", params, evidence, read_tokens, context_tokens=read_tokens)
    return dict(
        out, status=rec.status, reason=rec.reason, rolled_back=rec.rollback_triggered, rollback_ok=rec.rollback_ok,
        policy_changed=host.policy.label() != policy_before,
        tokens=read_tokens + rec.isolation_tokens + _rollback_tokens(host, rec),
    )


def run_arm(label: str, host_name: str, seed: int, run: int, pools: Dict[str, List[PoolPacket]],
            order: List[int], cache: EvalCache, n_tasks: int = TASKS) -> Dict[str, Any]:
    """Work through the task sequence; return per-task successes/tokens and slot records."""
    _name, delivery, selection, view = ARMS[label]
    pool = pools["shuffled" if view == "shuffled" else "plain"]
    packets = [p.packet for p in pool]
    run_seed = cache.run_seed
    model = StubStrategyModel() if delivery == "stub" else None
    host = Host(Case(run_seed, SLOT_TASKS[0]), model)
    host.policy = start_policy(host_name)
    untried = list(range(POOL_SIZE))
    own: Dict[str, List[int]] = {}
    successes: List[int] = []
    task_tokens: List[int] = []
    slot_tokens: List[int] = []
    selection_tokens: List[int] = []
    slots: List[Dict[str, Any]] = []
    for t in range(1, n_tasks + 1):
        s_tok = sel_tok = 0
        if delivery is not None and t in SLOT_TASKS:
            k = SLOT_TASKS.index(t)
            tie_rng = random.Random(stable_int("tie", seed, run, k))
            if selection in ENVELOPE_READING:
                sel_tok = sum(approx_tokens(envelope_of(packets[i])) for i in untried)
            if selection == "random":
                idx = order[k]
            elif selection == "history":
                idx = select_history(untried, packets, own, tie_rng)
            else:
                idx = select_claim(untried, packets, tie_rng)
            assert idx in untried, "a packet is never tried twice in a run"
            untried.remove(idx)
            host.case = Case(run_seed, t)
            pkt = packets[idx]
            res = deliver_file(host, pkt) if delivery == "file" else deliver_protocol(host, pkt)
            if res["has_result"] and res["reply_ref"] == pkt["id"]:
                src = source_of(pkt) or ""
                counts = own.setdefault(src, [0, 0])
                counts[0 if res["status"] == "absorbed" else 1] += 1
            s_tok = res["tokens"]
            slots.append({
                "slot": k, "task": t, "packet_id": pkt["id"], "family": pool[idx].family, "peer": pool[idx].peer,
                "source_ref": source_of(pkt), "status": res["status"],
                "content_policy": res["reason"].startswith("content_policy:"),
                "rolled_back": res["rolled_back"], "rollback_ok": res["rollback_ok"],
                "policy_changed": res["policy_changed"],
                "attributed": bool(res["policy_changed"] and res["has_result"] and res["reply_ref"] == pkt["id"]
                                   and res["status"] == "absorbed"),
                "tokens": s_tok, "selection_tokens": sel_tok,
            })
        succ, tok = cache.task(host.policy, t)
        successes.append(succ)
        task_tokens.append(tok + s_tok + sel_tok)
        slot_tokens.append(s_tok)
        selection_tokens.append(sel_tok)
    return {
        "arm": label, "host": host_name, "seed": seed, "run": run,
        "successes": successes, "tokens": task_tokens, "slot_tokens": slot_tokens,
        "selection_tokens": selection_tokens, "slots": slots, "final_policy": host.policy.label(),
    }


# ---------------------------------------------------------------------------
# Endpoints (§8)
# ---------------------------------------------------------------------------


def plateau_window(n_tasks: int) -> Tuple[int, int]:
    """1-based inclusive task range of the plateau window: T151-T200 for 200 tasks."""
    width = min(PLATEAU_TASKS, n_tasks)
    return n_tasks - width + 1, n_tasks


def window_successes(successes: Sequence[int]) -> List[int]:
    n_windows = len(successes) // WINDOW_TASKS
    return [sum(successes[i * WINDOW_TASKS:(i + 1) * WINDOW_TASKS]) for i in range(n_windows)]


def plateau_reached(window_succ: Sequence[int], plateau_num: int, plateau_den: int) -> Optional[int]:
    """§8.3: smallest 1-based w with SR(Wj) >= plateau_SR - delta for j = w..w+2
    (truncated at the last window). Exact rational arithmetic. None if no w."""
    ref = Fraction(plateau_num, plateau_den) - DELTA
    den = WINDOW_TASKS * EPISODES_PER_TASK
    ok = [Fraction(s, den) >= ref for s in window_succ]
    n = len(ok)
    for w in range(n):
        if all(ok[w:min(w + 3, n)]):
            return w + 1
    return None


def endpoints(arm_run: Dict[str, Any]) -> Dict[str, Any]:
    succ = arm_run["successes"]
    n = len(succ)
    lo, hi = plateau_window(n)
    plateau_num = sum(succ[lo - 1:hi])
    plateau_den = (hi - lo + 1) * EPISODES_PER_TASK
    wins = window_successes(succ)
    w = plateau_reached(wins, plateau_num, plateau_den)
    censored = w is None
    ttp = n if censored else WINDOW_TASKS * w
    toks = arm_run["tokens"]
    slots = arm_run["slots"]

    def count(family: str, what: str) -> int:
        if what == "selected":
            return sum(1 for s in slots if s["family"] == family)
        if what == "absorbed":
            return sum(1 for s in slots if s["family"] == family and s["status"] == "absorbed")
        return sum(1 for s in slots if s["family"] == family and s["rolled_back"])

    first_eff = next((i for i, s in enumerate(slots) if s["family"] == "effective" and s["status"] == "absorbed"), None)
    changes = [s for s in slots if s["policy_changed"]]
    return {
        "plateau_num": plateau_num,
        "plateau_den": plateau_den,
        "plateau_sr": plateau_num / plateau_den,
        "window_successes": wins,
        "auc_num": sum(wins),
        "auc": (sum(wins) / (len(wins) * WINDOW_TASKS * EPISODES_PER_TASK)) if wins else None,
        "plateau_window_reached": w,
        "plateau_not_reached": censored,
        "tasks_to_plateau": ttp,
        "tokens_to_plateau": sum(toks[:ttp]),
        "total_tokens": sum(toks),
        "selection_tokens": sum(arm_run["selection_tokens"]),
        "slot_tokens": sum(arm_run["slot_tokens"]),
        "absorption": {f: {k: count(f, k) for k in ("selected", "absorbed", "rolled_back")}
                       for f in ("effective", "mediocre", "harmful")},
        "slots_before_first_effective": first_eff,
        "policy_changes": len(changes),
        "attributed_changes": sum(1 for s in changes if s["attributed"]),
        "content_policy_rejections": sum(1 for s in slots if s["content_policy"]),
        "rollbacks": sum(1 for s in slots if s["rolled_back"]),
        "rollbacks_ok": sum(1 for s in slots if s["rolled_back"] and s["rollback_ok"]),
        "slot_codes": [f"{s['family'][0]}:{s['status'][0]}" for s in slots],
        "final_policy": arm_run["final_policy"],
    }


def simulate_run(seed: int, run: int, n_tasks: int = TASKS, arms: Sequence[str] = ARM_ORDER,
                 hosts: Sequence[str] = HOSTS) -> Dict[str, Any]:
    """All arms on all hosts for one (seed, run). Returns endpoints per host and arm."""
    pool = build_pool(seed, run)
    pools = {"plain": pool, "shuffled": shuffle_envelopes(pool, seed, run)}
    order = random_order(seed, run)
    cache = EvalCache(run_seed_of(seed, run))
    out: Dict[str, Any] = {"seed": seed, "run": run, "hosts": {}}
    for host_name in hosts:
        out["hosts"][host_name] = {
            label: endpoints(run_arm(label, host_name, seed, run, pools, order, cache, n_tasks)) for label in arms
        }
    return out
