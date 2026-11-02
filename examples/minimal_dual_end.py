#!/usr/bin/env python3
"""
Minimal dual-endpoint absorption loop + four-arm comparison (single file).

In-process Sender/Receiver peers exchange dict messages (no network / HTTP /
multi-process). Implements SPEC.md v0.2 MUST absorption methods and
IMPLEMENTATION.md host-side practice (regression, effect_summary, isolation N,
four arms) for the tool-call timeout recovery priority task.

Hard gate (this experiment path): after absorb, Result MUST carry a compliant
effect_summary (non-empty note + metrics with baseline_sr and at least one of
regression_sr / isolation_sr). Missing/incomplete summary => status rejected,
reason set, local injection/retrieval rolled back. metrics always record
declared_N, declared_threshold, rollback_triggered, rollback_action
(legacy isolation_n / drop_threshold mirrored for convenience).

Run from repo root:
  python3 examples/minimal_dual_end.py
  python3 examples/minimal_dual_end.py --seed 42 --trials 20 --isolation-n 3 --drop-threshold 0.10

Reproducible four-arm table (captured from a real run with the defaults above;
re-run the same command to match):

  arm              | SR    | ΔSR    | tool_calls | share_tok | imm_SR | del_SR | gap    | reg_mag | t_recover | backoff | abs_status
  -----------------+-------+--------+------------+-----------+--------+--------+--------+---------+-----------+---------+-----------
  no_sharing       | 0.600 | +0.000 | 46         | 0         | 0.500  | 0.700  | +0.200 | 0.000   | 0         | N       | -
  own_history      | 1.000 | +0.400 | 48         | 0         | 1.000  | 1.000  | +0.000 | 0.000   | 5         | Y       | -
  raw_trajectory   | 1.000 | +0.400 | 45         | 315       | 1.000  | 1.000  | +0.000 | -0.333  | 3         | Y       | absorbed
  distilled_packet | 0.950 | +0.350 | 46         | 176       | 1.000  | 0.950  | -0.050 | -0.167  | 3         | Y       | absorbed

  baseline SR (no_sharing)=0.600
"""

from __future__ import annotations

import argparse
import json
import random
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "schema"
MUST_METHODS = frozenset({"prompt_injection", "retrieval"})
ARM_NAMES = (
    "no_sharing",
    "own_history",
    "raw_trajectory",
    "distilled_packet",
)

# ---------------------------------------------------------------------------
# Schema validation (optional: skip when jsonschema is absent)
# ---------------------------------------------------------------------------


def optional_schema_validate(packet: dict, result: dict) -> str:
    """Validate against repo schemas. Returns a short status string."""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return "jsonschema not installed; skip validation"

    pkt_schema = json.loads((SCHEMA_DIR / "experience-packet.schema.json").read_text())
    res_schema = json.loads((SCHEMA_DIR / "absorption-result.schema.json").read_text())
    jsonschema.validate(packet, pkt_schema)
    jsonschema.validate(result, res_schema)
    return "experience-packet + absorption-result OK"


# ---------------------------------------------------------------------------
# Simulated tool-call timeout / recovery
# ---------------------------------------------------------------------------


@dataclass
class Policy:
    """Local tool-call policy. backoff=True enables exponential backoff recovery."""

    max_attempts: int = 3
    backoff: bool = False
    drop_stale_partials: bool = False
    injected_tokens: int = 0


@dataclass
class TrialResult:
    success: bool
    tool_calls: int
    timed_out: bool
    used_backoff: bool
    notes: List[str] = field(default_factory=list)
    tokens: int = 0


def _timeout_prob(attempt: int, backoff: bool) -> float:
    """Timeout probability per attempt. Backoff lowers later-attempt risk."""
    base = 0.78
    if not backoff:
        return base
    return max(0.12, base * (0.45 ** attempt))


def run_web_search(policy: Policy, rng: random.Random) -> TrialResult:
    """
    Simulate one web_search under policy.
    Naive immediate retries often enter a failing retry_loop.
    Exponential backoff recovers more often.
    """
    notes: List[str] = []
    tool_calls = 0
    timed_out_any = False
    used_backoff = False

    for attempt in range(policy.max_attempts):
        tool_calls += 1
        if rng.random() < _timeout_prob(attempt, policy.backoff):
            timed_out_any = True
            notes.append(f"attempt {attempt + 1}: timeout")
            if policy.backoff and attempt < policy.max_attempts - 1:
                used_backoff = True
                notes.append(f"attempt {attempt + 1}: exponential backoff")
            continue
        if policy.drop_stale_partials:
            notes.append(f"attempt {attempt + 1}: ok (stale partials dropped)")
        else:
            notes.append(f"attempt {attempt + 1}: ok")
        tokens = tool_calls * 12 + policy.injected_tokens
        return TrialResult(
            success=True,
            tool_calls=tool_calls,
            timed_out=timed_out_any,
            used_backoff=used_backoff,
            notes=notes,
            tokens=tokens,
        )

    notes.append("retry_loop exceeded without recovery")
    tokens = tool_calls * 12 + policy.injected_tokens
    return TrialResult(
        success=False,
        tool_calls=tool_calls,
        timed_out=True,
        used_backoff=used_backoff,
        notes=notes,
        tokens=tokens,
    )


def run_trials(policy: Policy, n: int, rng: random.Random) -> Dict[str, Any]:
    """Run n independent trials; return aggregate metrics."""
    results = [run_web_search(policy, rng) for _ in range(n)]
    successes = sum(1 for r in results if r.success)
    total_calls = sum(r.tool_calls for r in results)
    total_tokens = sum(r.tokens for r in results)
    return {
        "n": n,
        "successes": successes,
        "success_rate": successes / n if n else 0.0,
        "tool_calls": total_calls,
        "tokens": total_tokens,
        "avg_tool_calls": total_calls / n if n else 0.0,
        "results": results,
    }


def naive_policy() -> Policy:
    return Policy(max_attempts=3, backoff=False, drop_stale_partials=False, injected_tokens=0)


def recovered_policy() -> Policy:
    return Policy(
        max_attempts=3,
        backoff=True,
        drop_stale_partials=True,
        injected_tokens=48,
    )


def raw_trajectory_example(rng: Optional[random.Random] = None) -> Dict[str, Any]:
    """Full raw trajectory (noisy) for the raw-share arm — not distilled."""
    rng = rng or random.Random(0)
    steps = []
    for i in range(1, 4):
        steps.append(
            {
                "step": i,
                "action": "web_search",
                "args": {
                    "q": "example query",
                    "max_results": 3,
                    "nonce": rng.randint(0, 10_000),
                },
                "observation": "timeout" if i < 3 else "partial_hits",
                "latency_ms": 800 + i * 200 + rng.randint(0, 100),
                "debug": {"stack": f"frame-{i}", "cache_age_s": 45 + i},
            }
        )
    steps.append(
        {
            "step": 4,
            "action": "retry",
            "args": {"immediate": True},
            "observation": "retry_loop_fail",
            "note": "no backoff applied",
        }
    )
    steps.append(
        {
            "step": 5,
            "action": "retry",
            "args": {
                "backoff_ms": 200,
                "factor": 2,
                "drop_partials_older_than_s": 30,
            },
            "observation": "ok",
            "note": "exponential backoff recovered",
        }
    )
    return {
        "scenario": "tool_call_timeout on web_search",
        "trajectory": steps,
        "outcome": "recovered_after_backoff",
    }


def discover_from_own_history(
    budget_trials: int,
    rng: random.Random,
) -> Tuple[Policy, Dict[str, Any]]:
    """Own-history arm: explore then adopt backoff if enough timeouts seen."""
    policy = naive_policy()
    stats = run_trials(policy, budget_trials, rng)
    timeout_fails = sum(1 for r in stats["results"] if (not r.success) and r.timed_out)
    adopted = timeout_fails >= max(2, budget_trials // 5)
    if adopted:
        policy = recovered_policy()
    meta = {
        "explored_trials": budget_trials,
        "timeout_fails": timeout_fails,
        "adopted_backoff": adopted,
        "explore_success_rate": stats["success_rate"],
        "explore_tool_calls": stats["tool_calls"],
        "explore_tokens": stats["tokens"],
    }
    return policy, meta


# ---------------------------------------------------------------------------
# Experience Packet construction
# ---------------------------------------------------------------------------


def new_id(prefix: str = "pkt") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def approx_payload_tokens(obj: Any) -> int:
    """Rough token proxy: UTF-8 JSON bytes / 4."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return max(1, len(raw.encode("utf-8")) // 4)


def distill_experience_packet(
    *,
    method: str = "prompt_injection",
    packet_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Distill failure_mode + successful recovery only.
    Does not embed the full raw trajectory (small distilled arm payload).
    """
    allowed = MUST_METHODS | {
        "local_adapter_hint",
        "trajectory_replay",
        "parameter_delta",
        "other",
    }
    if method not in allowed:
        raise ValueError(f"unknown absorption.method: {method}")

    hint = (
        "prepend: 'On timeout, apply exponential backoff and drop results >30s old'"
        if method == "prompt_injection"
        else "index under timeout_handling: exponential backoff + drop stale partials"
    )
    target = (
        "system_or_tool_instruction"
        if method == "prompt_injection"
        else "local_strategy_store"
    )

    return {
        "id": packet_id or new_id("pkt"),
        "type": "failure_mode",
        "content": {
            "trigger": "tool_call_timeout on web_search",
            "observed": "retry_loop exceeded 3 attempts without backoff",
            "compressed_signal": (
                "insert exponential backoff before third retry; "
                "discard partial results older than 30s"
            ),
        },
        "absorption": {
            "method": method,
            "target": target,
            "hint": hint,
        },
        "expiry": {
            "condition": "after_local_absorption_or_24h",
            "discard_if": "constraint_set no longer includes web_search",
        },
        "verification_claim": {
            "expected_effect": "fewer unbounded retry loops after tool_call_timeout",
            "replay_hint": "replay against local web_search timeout scenario",
        },
    }


def wrap_raw_trajectory_as_share(
    trajectory: Dict[str, Any],
    *,
    packet_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Raw-trajectory arm payload: full trajectory for comparison.
    Shaped as Experience Packet so the receiver can emit Absorption Result;
    content keeps noisy steps (not distilled).
    """
    return {
        "id": packet_id or new_id("pkt-raw"),
        "type": "tool_trajectory",
        "content": {
            "raw_trajectory": trajectory,
            "compressed_signal": "full trajectory shared; receiver must extract recovery",
        },
        "absorption": {
            "method": "prompt_injection",
            "target": "system_or_tool_instruction",
            "hint": "derive timeout recovery from shared raw trajectory steps",
        },
        "expiry": {"condition": "after_local_absorption_or_24h"},
    }


def extract_recovery_from_raw(trajectory: Dict[str, Any]) -> bool:
    """Heuristic: raw share yields recovery if a backoff step succeeded."""
    steps: List[Dict[str, Any]] = trajectory.get("trajectory") or []
    for step in steps:
        args = step.get("args") or {}
        if step.get("observation") == "ok" and (
            "backoff_ms" in args or "factor" in args
        ):
            return True
        note = (step.get("note") or "").lower()
        if "backoff" in note and "recovered" in note:
            return True
    return trajectory.get("outcome") == "recovered_after_backoff"


# ---------------------------------------------------------------------------
# effect_summary hard gate (experiment path)
# ---------------------------------------------------------------------------


ROLLBACK_ACTION_NONE = "none"
ROLLBACK_ACTION_CLEAR = "clear_injection_remove_retrieval"


def effect_summary_is_compliant(result: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Compliant iff note is non-empty and metrics include baseline_sr plus at
    least one post-absorption SR (regression_sr or isolation_sr).
    """
    es = result.get("effect_summary")
    if not isinstance(es, dict):
        return False, "missing effect_summary object"
    note = es.get("note")
    if not isinstance(note, str) or not note.strip():
        return False, "effect_summary.note missing or empty"
    metrics = es.get("metrics")
    if not isinstance(metrics, dict):
        return False, "effect_summary.metrics missing or not an object"
    if "baseline_sr" not in metrics:
        return False, "metrics.baseline_sr missing"
    if "regression_sr" not in metrics and "isolation_sr" not in metrics:
        return False, "metrics need regression_sr and/or isolation_sr"
    return True, "ok"


def stamp_isolation_declaration(
    metrics: Dict[str, Any],
    *,
    declared_n: int,
    declared_threshold: float,
    rollback_triggered: bool,
    rollback_action: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Write required isolation declaration fields into effect_summary.metrics.
    Required keys: declared_N, declared_threshold, rollback_triggered, rollback_action.
    Also mirrors legacy isolation_n / drop_threshold.
    """
    action = rollback_action
    if action is None:
        action = (
            ROLLBACK_ACTION_CLEAR if rollback_triggered else ROLLBACK_ACTION_NONE
        )
    out = dict(metrics)
    out["declared_N"] = int(declared_n)
    out["declared_threshold"] = float(declared_threshold)
    out["rollback_triggered"] = bool(rollback_triggered)
    out["rollback_action"] = action
    # Legacy mirrors (optional convenience; not a substitute for declared_*).
    out["isolation_n"] = int(declared_n)
    out["drop_threshold"] = float(declared_threshold)
    return out


def enforce_effect_summary_or_reject(
    state: "ReceiverState",
    result: Dict[str, Any],
    *,
    isolation_n: int,
    drop_threshold: float,
) -> Dict[str, Any]:
    """
    Hard gate at absorb-path end.
    Non-compliant effect_summary -> rejected + reason + rollback (clear
    injection / remove retrieval). Rejection itself carries a compliant summary.
    """
    ok, why = effect_summary_is_compliant(result)
    if ok:
        es = dict(result["effect_summary"])
        metrics = dict(es.get("metrics") or {})
        triggered = bool(metrics.get("rollback_triggered", False))
        action = metrics.get("rollback_action")
        if not isinstance(action, str) or not action:
            action = (
                ROLLBACK_ACTION_CLEAR if triggered else ROLLBACK_ACTION_NONE
            )
        metrics = stamp_isolation_declaration(
            metrics,
            declared_n=isolation_n,
            declared_threshold=drop_threshold,
            rollback_triggered=triggered,
            rollback_action=action,
        )
        es["metrics"] = metrics
        out = dict(result)
        out["effect_summary"] = es
        return out

    _rollback(state)
    old_metrics: Dict[str, Any] = {}
    old_es = result.get("effect_summary")
    if isinstance(old_es, dict) and isinstance(old_es.get("metrics"), dict):
        old_metrics = dict(old_es["metrics"])

    metrics = dict(old_metrics)
    if "baseline_sr" not in metrics:
        metrics["baseline_sr"] = float(old_metrics.get("baseline_sr", 0.0) or 0.0)
    if "regression_sr" not in metrics and "isolation_sr" not in metrics:
        metrics["regression_sr"] = float(old_metrics.get("regression_sr", 0.0) or 0.0)
        metrics["isolation_sr"] = float(old_metrics.get("isolation_sr", 0.0) or 0.0)
    metrics = stamp_isolation_declaration(
        metrics,
        declared_n=isolation_n,
        declared_threshold=drop_threshold,
        rollback_triggered=True,
        rollback_action=ROLLBACK_ACTION_CLEAR,
    )

    return {
        "packet_ref": result.get("packet_ref") or "",
        "status": "rejected",
        "reason": (
            f"effect_summary non-compliant ({why}); "
            "auto-rejected and rolled back (cleared injection / removed retrieval)"
        ),
        "effect_summary": {
            "note": (
                "hard gate failed: missing compliant effect_summary; "
                "absorption not retained"
            ),
            "metrics": metrics,
        },
    }


# ---------------------------------------------------------------------------
# Receiver / Sender (in-process dual end)
# ---------------------------------------------------------------------------


@dataclass
class ReceiverState:
    name: str = "receiver"
    policy: Policy = field(default_factory=naive_policy)
    # retrieval store: keyed by packet id (and a strategy key for lookup demos)
    retrieval_store: Dict[str, Any] = field(default_factory=dict)
    prompt_prefix: str = ""
    _rollback_policy: Optional[Policy] = field(default=None, repr=False)
    _rollback_prompt: str = field(default="", repr=False)
    _rollback_store: Dict[str, Any] = field(default_factory=dict, repr=False)


def _snapshot(state: ReceiverState) -> None:
    state._rollback_policy = deepcopy(state.policy)
    state._rollback_prompt = state.prompt_prefix
    state._rollback_store = dict(state.retrieval_store)


def _rollback(state: ReceiverState) -> None:
    """Clear injection / remove retrieval entries after failed isolation."""
    if state._rollback_policy is not None:
        state.policy = deepcopy(state._rollback_policy)
    state.prompt_prefix = state._rollback_prompt
    state.retrieval_store = dict(state._rollback_store)


class Receiver:
    """In-process peer that absorbs Experience Packets under SPEC MUST rules."""

    def __init__(
        self,
        *,
        isolation_n: int = 3,
        regression_drop_threshold: float = 0.10,
        name: str = "receiver",
    ) -> None:
        self.state = ReceiverState(name=name)
        self.isolation_n = isolation_n
        # Reject + rollback if isolation SR drops this far below baseline.
        self.regression_drop_threshold = regression_drop_threshold

    def retrieve(self, packet_id: str) -> Optional[Any]:
        """Look up a previously absorbed packet by id from the retrieval store."""
        return self.state.retrieval_store.get(packet_id)

    def absorb(
        self,
        packet: Dict[str, Any],
        rng: random.Random,
        *,
        regression_trials: int = 8,
    ) -> Dict[str, Any]:
        """
        Receive → MUST method → local regression → isolation N → promote or reject.
        Always returns Absorption Result; rejected/isolated include reason.
        Forces effect_summary (note + metrics) for this experiment path.
        Final Result is hard-gated: missing/incomplete effect_summary becomes
        rejected + rollback (absorption not retained).
        """
        packet_id = packet["id"]
        absorption = packet.get("absorption") or {}
        method = absorption.get("method", "")

        def _finalize(result: Dict[str, Any]) -> Dict[str, Any]:
            return enforce_effect_summary_or_reject(
                self.state,
                result,
                isolation_n=self.isolation_n,
                drop_threshold=self.regression_drop_threshold,
            )

        if method not in MUST_METHODS:
            return _finalize(
                {
                    "packet_ref": packet_id,
                    "status": "rejected",
                    "reason": (
                        f"unsupported absorption.method '{method}'; "
                        f"receiver MUST support only {sorted(MUST_METHODS)}"
                    ),
                    "effect_summary": {
                        "note": "no local change applied; method not executable",
                        "metrics": stamp_isolation_declaration(
                            {
                                "method": method,
                                "payload_tokens": approx_payload_tokens(packet),
                                # No trials run; SR placeholders keep the hard gate satisfied.
                                "baseline_sr": 0.0,
                                "regression_sr": 0.0,
                                "isolation_sr": 0.0,
                            },
                            declared_n=self.isolation_n,
                            declared_threshold=self.regression_drop_threshold,
                            rollback_triggered=False,
                            rollback_action=ROLLBACK_ACTION_NONE,
                        ),
                    },
                }
            )

        baseline = run_trials(self.state.policy, regression_trials, rng)
        baseline_sr = baseline["success_rate"]

        _snapshot(self.state)

        hint = absorption.get("hint") or ""
        content = packet.get("content") or {}
        prompt_before = self.state.prompt_prefix
        store_before_keys = sorted(self.state.retrieval_store.keys())

        raw = content.get("raw_trajectory")
        applied = False
        if raw is not None:
            if extract_recovery_from_raw(raw):
                self._apply_recovery(method, hint or "backoff from raw trajectory", packet)
                applied = True
        else:
            self._apply_recovery(method, hint, packet)
            applied = True

        prompt_after = self.state.prompt_prefix
        store_after_keys = sorted(self.state.retrieval_store.keys())

        # Immediate regression of the same failure scenario.
        regression = run_trials(self.state.policy, regression_trials, rng)
        reg_sr = regression["success_rate"]

        # Isolation N before promotion.
        isolation = run_trials(self.state.policy, self.isolation_n, rng)
        iso_sr = isolation["success_rate"]

        drop = baseline_sr - iso_sr
        payload_tokens = approx_payload_tokens(packet)

        metrics = stamp_isolation_declaration(
            {
                "baseline_sr": round(baseline_sr, 4),
                "regression_sr": round(reg_sr, 4),
                "isolation_sr": round(iso_sr, 4),
                "regression_drop": round(drop, 4),
                "tool_calls_isolation": isolation["tool_calls"],
                "tokens_isolation": isolation["tokens"],
                "payload_tokens": payload_tokens,
                "adapter": method,
                "applied": applied,
                "prompt_before": prompt_before or "(empty)",
                "prompt_after": prompt_after or "(empty)",
                "prompt_changed": prompt_before != prompt_after,
                "retrieval_keys_before": ",".join(store_before_keys) or "(none)",
                "retrieval_keys_after": ",".join(store_after_keys) or "(none)",
            },
            declared_n=self.isolation_n,
            declared_threshold=self.regression_drop_threshold,
            rollback_triggered=False,
            rollback_action=ROLLBACK_ACTION_NONE,
        )

        # Performance drop past threshold → rejected + rollback.
        if drop > self.regression_drop_threshold and iso_sr < baseline_sr:
            _rollback(self.state)
            metrics = stamp_isolation_declaration(
                metrics,
                declared_n=self.isolation_n,
                declared_threshold=self.regression_drop_threshold,
                rollback_triggered=True,
                rollback_action=ROLLBACK_ACTION_CLEAR,
            )
            return _finalize(
                {
                    "packet_ref": packet_id,
                    "status": "rejected",
                    "reason": (
                        f"isolation SR {iso_sr:.3f} dropped below baseline {baseline_sr:.3f} "
                        f"by {drop:.3f} (threshold {self.regression_drop_threshold})"
                    ),
                    "effect_summary": {
                        "note": "change rolled back after isolation trials "
                        "(cleared injection / removed retrieval entry)",
                        "metrics": metrics,
                    },
                }
            )

        # Inconclusive isolation → keep isolated (rolled back from active policy).
        if iso_sr == 0.0 and reg_sr <= baseline_sr:
            _rollback(self.state)
            metrics = stamp_isolation_declaration(
                metrics,
                declared_n=self.isolation_n,
                declared_threshold=self.regression_drop_threshold,
                rollback_triggered=True,
                rollback_action=ROLLBACK_ACTION_CLEAR,
            )
            return _finalize(
                {
                    "packet_ref": packet_id,
                    "status": "isolated",
                    "reason": (
                        "content retained for inspection but not promoted; "
                        f"isolation_sr={iso_sr:.3f} showed no improvement"
                    ),
                    "effect_summary": {
                        "note": "held in isolation; active policy unchanged",
                        "metrics": metrics,
                    },
                }
            )

        note = (
            f"{method} applied; isolation {self.isolation_n} trials "
            f"SR {iso_sr:.3f} (baseline {baseline_sr:.3f})"
        )
        return _finalize(
            {
                "packet_ref": packet_id,
                "status": "absorbed",
                "effect_summary": {
                    "note": note,
                    "metrics": metrics,
                },
            }
        )

    def _apply_recovery(
        self,
        method: str,
        hint: str,
        packet: Dict[str, Any],
    ) -> None:
        self.state.policy = recovered_policy()
        if method == "prompt_injection":
            self.state.prompt_prefix = hint
            self.state.policy.injected_tokens = max(
                self.state.policy.injected_tokens, len(hint) // 4
            )
        elif method == "retrieval":
            # Store under packet id for by-id retrieval; also keep a strategy key.
            entry = {
                "hint": hint,
                "content": packet.get("content"),
                "type": packet.get("type"),
            }
            self.state.retrieval_store[packet["id"]] = entry
            self.state.retrieval_store["timeout_handling"] = hint
            self.state.policy.injected_tokens = max(
                self.state.policy.injected_tokens, len(hint) // 4
            )


class Sender:
    """In-process peer that distills and sends Experience Packets."""

    def __init__(self, name: str = "sender") -> None:
        self.name = name
        self.policy = recovered_policy()

    def send_distilled(
        self,
        method: str = "prompt_injection",
        packet_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return distill_experience_packet(method=method, packet_id=packet_id)

    def send_raw_trajectory(self, rng: random.Random) -> Dict[str, Any]:
        return wrap_raw_trajectory_as_share(raw_trajectory_example(rng))


# ---------------------------------------------------------------------------
# Four-arm comparison
# ---------------------------------------------------------------------------


def _eval(policy: Policy, trials: int, rng: random.Random) -> Dict[str, Any]:
    return run_trials(policy, trials, rng)


def run_four_arm(
    *,
    trials: int = 20,
    seed: int = 42,
    isolation_n: int = 3,
    method: str = "prompt_injection",
    regression_drop_threshold: float = 0.10,
) -> Dict[str, Any]:
    """Four arms under a fixed evaluation budget (`trials` each)."""
    base_rng = random.Random(seed)
    arm_seeds = {name: base_rng.randint(0, 10**9) for name in ARM_NAMES}

    sender = Sender()
    results: Dict[str, Any] = {}
    absorption_samples: Dict[str, Optional[Dict[str, Any]]] = {}
    packet_samples: Dict[str, Optional[Dict[str, Any]]] = {}

    # --- Arm 1: no sharing ---
    rng = random.Random(arm_seeds["no_sharing"])
    recv = Receiver(
        isolation_n=isolation_n,
        regression_drop_threshold=regression_drop_threshold,
        name="recv-no-share",
    )
    half = max(1, trials // 2)
    imm = _eval(recv.state.policy, half, rng)
    delayed = _eval(recv.state.policy, trials - half, rng)
    full_sr = (imm["successes"] + delayed["successes"]) / trials
    results["no_sharing"] = {
        "success_rate": full_sr,
        "tool_calls": imm["tool_calls"] + delayed["tool_calls"],
        "tokens": imm["tokens"] + delayed["tokens"],
        "extra_share_tokens": 0,
        "immediate_sr": imm["success_rate"],
        "delayed_sr": delayed["success_rate"],
        "imm_vs_delayed_gap": round(delayed["success_rate"] - imm["success_rate"], 4),
        "regression_magnitude": 0.0,
        "recovery_trials": 0,
        "policy_backoff": recv.state.policy.backoff,
        "absorption": None,
    }
    absorption_samples["no_sharing"] = None
    packet_samples["no_sharing"] = None
    baseline_sr = results["no_sharing"]["success_rate"]

    # --- Arm 2: own history only ---
    rng = random.Random(arm_seeds["own_history"])
    explore_n = max(4, trials // 4)
    eval_n = trials - explore_n
    policy, meta = discover_from_own_history(explore_n, rng)
    imm_n = max(1, eval_n // 2)
    delayed_n = eval_n - imm_n
    imm = _eval(policy, imm_n, rng)
    delayed = _eval(policy, delayed_n, rng)
    sr = (imm["successes"] + delayed["successes"]) / eval_n if eval_n else 0.0
    results["own_history"] = {
        "success_rate": sr,
        "tool_calls": meta["explore_tool_calls"] + imm["tool_calls"] + delayed["tool_calls"],
        "tokens": meta["explore_tokens"] + imm["tokens"] + delayed["tokens"],
        "extra_share_tokens": 0,
        "immediate_sr": imm["success_rate"],
        "delayed_sr": delayed["success_rate"],
        "imm_vs_delayed_gap": round(delayed["success_rate"] - imm["success_rate"], 4),
        "regression_magnitude": 0.0,
        "recovery_trials": explore_n if meta["adopted_backoff"] else 0,
        "policy_backoff": policy.backoff,
        "absorption": None,
        "explore": meta,
    }
    absorption_samples["own_history"] = None
    packet_samples["own_history"] = None

    # --- Arm 3: raw trajectory share ---
    rng = random.Random(arm_seeds["raw_trajectory"])
    recv = Receiver(
        isolation_n=isolation_n,
        regression_drop_threshold=regression_drop_threshold,
        name="recv-raw",
    )
    raw_packet = sender.send_raw_trajectory(rng)
    share_tokens = approx_payload_tokens(raw_packet)
    abs_result = recv.absorb(raw_packet, rng, regression_trials=max(6, isolation_n * 2))
    absorption_samples["raw_trajectory"] = abs_result
    packet_samples["raw_trajectory"] = raw_packet

    metrics = (abs_result.get("effect_summary") or {}).get("metrics") or {}
    imm_sr = float(metrics.get("isolation_sr", 0.0))
    delayed = _eval(recv.state.policy, trials, rng)
    reg_mag = float(metrics.get("regression_drop", 0.0))
    recovered = abs_result.get("status") == "absorbed"
    results["raw_trajectory"] = {
        "success_rate": delayed["success_rate"],
        "tool_calls": delayed["tool_calls"] + int(metrics.get("tool_calls_isolation", 0)),
        "tokens": delayed["tokens"]
        + int(metrics.get("tokens_isolation", 0))
        + share_tokens,
        "extra_share_tokens": share_tokens,
        "immediate_sr": imm_sr,
        "delayed_sr": delayed["success_rate"],
        "imm_vs_delayed_gap": round(delayed["success_rate"] - imm_sr, 4),
        "regression_magnitude": reg_mag,
        "recovery_trials": isolation_n if recovered else 0,
        "policy_backoff": recv.state.policy.backoff,
        "absorption": abs_result,
    }

    # --- Arm 4: distilled Experience Packet ---
    rng = random.Random(arm_seeds["distilled_packet"])
    recv = Receiver(
        isolation_n=isolation_n,
        regression_drop_threshold=regression_drop_threshold,
        name="recv-packet",
    )
    packet = sender.send_distilled(method=method)
    share_tokens = approx_payload_tokens(packet)
    abs_result = recv.absorb(packet, rng, regression_trials=max(6, isolation_n * 2))
    absorption_samples["distilled_packet"] = abs_result
    packet_samples["distilled_packet"] = packet

    metrics = (abs_result.get("effect_summary") or {}).get("metrics") or {}
    imm_sr = float(metrics.get("isolation_sr", 0.0))
    delayed = _eval(recv.state.policy, trials, rng)
    # Delayed re-eval after a fresh context window (cross-context gap).
    delayed2 = _eval(recv.state.policy, trials, rng)
    metrics["delayed_retest_sr"] = round(delayed2["success_rate"], 4)
    metrics["imm_vs_delayed_gap"] = round(delayed2["success_rate"] - imm_sr, 4)
    if abs_result.get("effect_summary"):
        abs_result["effect_summary"]["metrics"] = metrics

    reg_mag = float(metrics.get("regression_drop", 0.0))
    recovered = abs_result.get("status") == "absorbed"
    results["distilled_packet"] = {
        "success_rate": delayed["success_rate"],
        "tool_calls": delayed["tool_calls"] + int(metrics.get("tool_calls_isolation", 0)),
        "tokens": delayed["tokens"]
        + int(metrics.get("tokens_isolation", 0))
        + share_tokens,
        "extra_share_tokens": share_tokens,
        "immediate_sr": imm_sr,
        "delayed_sr": delayed2["success_rate"],
        "imm_vs_delayed_gap": round(delayed2["success_rate"] - imm_sr, 4),
        "regression_magnitude": reg_mag,
        "recovery_trials": isolation_n if recovered else 0,
        "policy_backoff": recv.state.policy.backoff,
        "absorption": abs_result,
        "retrieval_by_id": recv.retrieve(packet["id"]) is not None
        if method == "retrieval"
        else None,
    }

    for name, row in results.items():
        row["delta_sr"] = round(row["success_rate"] - baseline_sr, 4)
        row["extra_tool_calls_vs_baseline"] = (
            row["tool_calls"] - results["no_sharing"]["tool_calls"]
        )

    return {
        "config": {
            "trials": trials,
            "seed": seed,
            "isolation_n": isolation_n,
            "method": method,
            "regression_drop_threshold": regression_drop_threshold,
        },
        "baseline_sr": baseline_sr,
        "arms": results,
        "absorption_samples": absorption_samples,
        "packet_samples": packet_samples,
    }


def format_table(report: Dict[str, Any]) -> str:
    """Pretty-print comparison table."""
    arms: Dict[str, Any] = report["arms"]
    headers = [
        "arm",
        "SR",
        "ΔSR",
        "tool_calls",
        "share_tok",
        "imm_SR",
        "del_SR",
        "gap",
        "reg_mag",
        "t_recover",
        "backoff",
        "abs_status",
    ]
    rows: List[List[str]] = []
    for name in ARM_NAMES:
        r = arms[name]
        abs_r = r.get("absorption")
        status = abs_r.get("status") if abs_r else "-"
        rows.append(
            [
                name,
                f"{r['success_rate']:.3f}",
                f"{r['delta_sr']:+.3f}",
                str(r["tool_calls"]),
                str(r["extra_share_tokens"]),
                f"{r['immediate_sr']:.3f}",
                f"{r['delayed_sr']:.3f}",
                f"{r['imm_vs_delayed_gap']:+.3f}",
                f"{r['regression_magnitude']:.3f}",
                str(r["recovery_trials"]),
                "Y" if r["policy_backoff"] else "N",
                str(status),
            ]
        )

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cols: List[str]) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols))

    lines = [
        fmt_row(headers),
        "-+-".join("-" * w for w in widths),
    ]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Demos + main
# ---------------------------------------------------------------------------


def demo_unsupported_reject(isolation_n: int) -> Dict[str, Any]:
    """Show explicit reject for non-MUST method (trajectory_replay)."""
    sender = Sender()
    recv = Receiver(isolation_n=isolation_n)
    bad = sender.send_distilled(method="prompt_injection")
    bad = dict(bad)
    bad["id"] = "pkt-reject-demo"
    bad["absorption"] = {
        "method": "trajectory_replay",
        "target": "local_replay_buffer",
        "hint": "replay steps",
    }
    return recv.absorb(bad, random.Random(0), regression_trials=4)


def demo_missing_effect_summary_reject(
    isolation_n: int = 3,
    drop_threshold: float = 0.10,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Prove intentional missing effect_summary → rejected + rollback.
    Returns (Absorption Result, diagnostics about local state after gate).
    """
    recv = Receiver(
        isolation_n=isolation_n,
        regression_drop_threshold=drop_threshold,
        name="recv-missing-es",
    )
    _snapshot(recv.state)
    recv.state.policy = recovered_policy()
    recv.state.prompt_prefix = "SHOULD_BE_CLEARED_BY_HARD_GATE"
    recv.state.retrieval_store["timeout_handling"] = "should_be_removed"
    noncompliant = {
        "packet_ref": "pkt-missing-es-demo",
        "status": "absorbed",
        # deliberately omit effect_summary
    }
    result = enforce_effect_summary_or_reject(
        recv.state,
        noncompliant,
        isolation_n=isolation_n,
        drop_threshold=drop_threshold,
    )
    diagnostics = {
        "prompt_prefix_after": recv.state.prompt_prefix,
        "policy_backoff_after": recv.state.policy.backoff,
        "retrieval_keys_after": sorted(recv.state.retrieval_store.keys()),
        "gate_status": result.get("status"),
        "rollback_triggered": (result.get("effect_summary") or {})
        .get("metrics", {})
        .get("rollback_triggered"),
        "rollback_action": (result.get("effect_summary") or {})
        .get("metrics", {})
        .get("rollback_action"),
    }
    return result, diagnostics


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Minimal dual-end absorption + four-arm compare (single file)"
    )
    p.add_argument("--trials", type=int, default=20, help="fixed eval budget per arm")
    p.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    p.add_argument(
        "--isolation-n", type=int, default=3, help="isolation trials before promote"
    )
    p.add_argument(
        "--method",
        choices=("prompt_injection", "retrieval"),
        default="prompt_injection",
        help="MUST absorption method for distilled packet arm",
    )
    p.add_argument(
        "--drop-threshold",
        type=float,
        default=0.10,
        help="reject+rollback if isolation SR drops this far below baseline (default 0.10)",
    )
    p.add_argument(
        "--skip-schema",
        action="store_true",
        help="skip optional jsonschema validation even if installed",
    )
    args = p.parse_args(argv)

    report = run_four_arm(
        trials=args.trials,
        seed=args.seed,
        isolation_n=args.isolation_n,
        method=args.method,
        regression_drop_threshold=args.drop_threshold,
    )

    cfg = report["config"]
    print("=== Dual-endpoint four-arm comparison ===")
    print(
        f"trials={cfg['trials']} seed={cfg['seed']} "
        f"isolation_n={cfg['isolation_n']} method={cfg['method']} "
        f"drop_threshold={cfg['regression_drop_threshold']}"
    )
    print(f"baseline SR (no_sharing)={report['baseline_sr']:.3f}")
    print()
    print(format_table(report))
    print()

    print("=== Absorption Result samples (Packet id / status / effect_summary / ΔSR) ===")
    for arm in ARM_NAMES:
        sample = report["absorption_samples"][arm]
        packet = report["packet_samples"][arm]
        delta = report["arms"][arm]["delta_sr"]
        print(f"\n-- {arm}  ΔSR={delta:+.3f} --")
        if sample is None:
            print("(no packet exchanged)")
        else:
            print(f"packet_id={packet['id'] if packet else '?'}")
            print(f"status={sample.get('status')}")
            print(
                "effect_summary="
                + json.dumps(sample.get("effect_summary"), ensure_ascii=False, indent=2)
            )

    reject_demo = demo_unsupported_reject(args.isolation_n)
    print("\n=== Unsupported method reject demo ===")
    print(json.dumps(reject_demo, indent=2, ensure_ascii=False))

    missing_es, missing_diag = demo_missing_effect_summary_reject(
        isolation_n=args.isolation_n,
        drop_threshold=args.drop_threshold,
    )
    print("\n=== Missing effect_summary hard-gate demo (must be rejected + rolled back) ===")
    print(json.dumps(missing_es, indent=2, ensure_ascii=False))
    print("diagnostics=" + json.dumps(missing_diag, indent=2, ensure_ascii=False))

    packet = (report.get("packet_samples") or {}).get("distilled_packet")
    abs_sample = report["absorption_samples"]["distilled_packet"]
    print("\n=== Distilled Experience Packet (arm 4) ===")
    print(json.dumps(packet, indent=2, ensure_ascii=False))

    if not args.skip_schema and packet is not None and abs_sample is not None:
        status = optional_schema_validate(packet, abs_sample)
        print(f"\n[schema] {status}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
