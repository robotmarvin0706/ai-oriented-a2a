"""Parse timeout-recovery strategy parameters out of Experience Packet content.

Non-normative, host-side. The parser reads *data* from a packet's ``content``
and returns policy parameters; it never treats the text as an instruction and
never reads ``absorption.hint`` (sender text that SPEC §2.1 keeps out of the
injected block). Content that does not state any parameter yields ``{}``, and
the caller must then leave its policy unchanged.

Sources, in this order (later sources only fill keys not already set):

1. ``content.recovery_policy``: a structured object, for example
   ``{"backoff": "exponential", "base_backoff_ms": 400, "max_attempts": 4,
   "drop_stale_partials": true, "stale_after_s": 30}``.
2. ``content.raw_trajectory``: a raw step log (the receiver has to extract
   the recovery decision itself).
3. Recommendation text: ``compressed_signal``, ``recommendation`` and string
   items of ``steps``. ``trigger`` and ``observed`` describe the failure, not
   the fix, so they are never parsed.

Returned keys (all optional): ``backoff`` (bool), ``base_backoff_ms`` (int),
``max_attempts`` (int, or ``None`` for unbounded), ``drop_stale_partials``
(bool), ``stale_after_s`` (int).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

PARAM_KEYS = ("backoff", "base_backoff_ms", "max_attempts", "drop_stale_partials", "stale_after_s")
TEXT_FIELDS = ("compressed_signal", "recommendation")
UNBOUNDED_WORDS = ("unbounded", "unlimited", "infinite", "inf", "forever", "none")

_NEG_BACKOFF = re.compile(
    r"\b(?:no|without|remove|removing|disable|disabling|skip|skipping|never use|drop)\s+"
    r"(?:any\s+|the\s+)?(?:exponential\s+)?back-?off|\bretry immediately\b|\bimmediate retr"
)
_POS_BACKOFF = re.compile(r"back-?off")
_BACKOFF_MS = re.compile(r"back-?off[^.;]*?(\d+)\s*ms")
_UNBOUNDED = re.compile(
    r"(?:retry|retries|retrying|keep retrying)[^.;]*?"
    r"(?:forever|indefinitely|unbounded|without (?:a |any )?limit|until (?:a |it |the )?(?:result|success|it succeeds))"
    r"|\b(?:infinite|unlimited|unbounded) retr"
)
_ATTEMPTS = re.compile(r"\b(\d+)\s+(?:total\s+)?attempts\b")
_RETRIES = re.compile(r"\b(\d+)\s+retr(?:y|ies)\b")
_ONCE = re.compile(r"\b(?:retry once|single retry|one retry)\b")
_NEG_STALE = re.compile(r"\b(?:keep|keeping|reuse|reusing|accept|accepting|use)\s+(?:the\s+)?(?:stale|partial|old)")
_POS_STALE = re.compile(r"\b(?:discard|discarding|drop|dropping|ignore|ignoring)\s+(?:the\s+)?(?:stale|partial|old|late)")
_STALE_S = re.compile(r"older than\s*(\d+)\s*s\b|>\s*(\d+)\s*s\b")


def _as_bool_backoff(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("none", "off", "no", "false", "disabled", "immediate"):
            return False
        if v in ("exponential", "linear", "jittered", "on", "yes", "true"):
            return True
    return None


def _as_attempts(value: Any) -> Tuple[bool, Optional[int]]:
    """(recognised, value); value None means unbounded."""
    if value is None:
        return True, None
    if isinstance(value, bool):
        return False, None
    if isinstance(value, int):
        return (True, value) if value >= 1 else (True, None)
    if isinstance(value, str) and value.strip().lower() in UNBOUNDED_WORDS:
        return True, None
    return False, None


def _from_structured(policy: Any, out: Dict[str, Any], evidence: List[str]) -> None:
    if not isinstance(policy, dict):
        return
    b = _as_bool_backoff(policy.get("backoff"))
    if b is not None:
        out["backoff"] = b
        evidence.append("recovery_policy.backoff")
    ms = policy.get("base_backoff_ms")
    if isinstance(ms, int) and not isinstance(ms, bool) and ms > 0:
        out["base_backoff_ms"] = ms
        evidence.append("recovery_policy.base_backoff_ms")
    if "max_attempts" in policy:
        ok, val = _as_attempts(policy.get("max_attempts"))
        if ok:
            out["max_attempts"] = val
            evidence.append("recovery_policy.max_attempts")
    d = policy.get("drop_stale_partials")
    if isinstance(d, bool):
        out["drop_stale_partials"] = d
        evidence.append("recovery_policy.drop_stale_partials")
    s = policy.get("stale_after_s")
    if isinstance(s, int) and not isinstance(s, bool) and s > 0:
        out["stale_after_s"] = s
        evidence.append("recovery_policy.stale_after_s")


def _steps_of(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        for key in ("trajectory", "steps"):
            if isinstance(raw.get(key), list):
                return [s for s in raw[key] if isinstance(s, dict)]
        return []
    if isinstance(raw, list):
        return [s for s in raw if isinstance(s, dict)]
    return []


def _from_raw(raw: Any, out: Dict[str, Any], evidence: List[str]) -> None:
    """Extract the recovery decision from a raw step log.

    Understands two shapes: ``examples/minimal_dual_end.py`` steps (``args``
    with ``backoff_ms`` / ``drop_partials_older_than_s``, ``observation``) and
    host episode logs (``wait_ms``, ``event``). Only waits that precede a
    successful step count as evidence of backoff.
    """
    steps = _steps_of(raw)
    found: Dict[str, Any] = {}
    waits: List[int] = []
    attempts = 0
    for step in steps:
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        event = str(step.get("event") or step.get("observation") or "").lower()
        if event in ("timeout", "ok", "partial_hits", "retry_loop_fail", "throttled", "stale_discarded", "stale_accepted"):
            attempts += 1 if event != "stale_discarded" else 0
        wait = step.get("wait_ms", args.get("backoff_ms"))
        if isinstance(wait, int) and not isinstance(wait, bool) and wait > 0:
            waits.append(wait)
        if event == "ok" and waits:
            found["backoff"] = True
            found["base_backoff_ms"] = min(waits)
        old = args.get("drop_partials_older_than_s")
        if isinstance(old, int) and not isinstance(old, bool) and old > 0:
            found["drop_stale_partials"] = True
            found["stale_after_s"] = old
        if event == "stale_discarded":
            found["drop_stale_partials"] = True
            age = step.get("stale_after_s")
            if isinstance(age, int) and not isinstance(age, bool) and age > 0:
                found["stale_after_s"] = age
    if found.get("backoff") and attempts > 0:
        found["max_attempts"] = attempts
    for key, value in found.items():
        if key not in out:
            out[key] = value
            evidence.append(f"raw_trajectory.{key}")


def _texts(content: Dict[str, Any]) -> Iterable[str]:
    for key in TEXT_FIELDS:
        value = content.get(key)
        if isinstance(value, str):
            yield value
    steps = content.get("steps")
    if isinstance(steps, list):
        for item in steps:
            if isinstance(item, str):
                yield item


def _from_text(text: str, out: Dict[str, Any], evidence: List[str]) -> None:
    t = " ".join(text.lower().split())
    found: Dict[str, Any] = {}
    if _NEG_BACKOFF.search(t):
        found["backoff"] = False
    elif _POS_BACKOFF.search(t):
        found["backoff"] = True
        m = _BACKOFF_MS.search(t)
        if m:
            found["base_backoff_ms"] = int(m.group(1))
    if _UNBOUNDED.search(t):
        found["max_attempts"] = None
    else:
        m = _ATTEMPTS.search(t)
        if m and int(m.group(1)) >= 1:
            found["max_attempts"] = int(m.group(1))
        else:
            m = _RETRIES.search(t)
            if m:
                found["max_attempts"] = int(m.group(1)) + 1
            elif _ONCE.search(t):
                found["max_attempts"] = 2
    if _NEG_STALE.search(t):
        found["drop_stale_partials"] = False
    elif _POS_STALE.search(t):
        found["drop_stale_partials"] = True
        m = _STALE_S.search(t)
        if m:
            found["stale_after_s"] = int(m.group(1) or m.group(2))
    for key, value in found.items():
        if key not in out:
            out[key] = value
            evidence.append(f"text.{key}")


def parse_recovery_params(content: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Return ``(params, evidence)`` parsed from packet ``content``.

    ``params`` is empty when the content states no recovery parameter (empty
    or unrelated content). ``evidence`` names where each key came from.
    """
    out: Dict[str, Any] = {}
    evidence: List[str] = []
    if not isinstance(content, dict):
        return out, evidence
    _from_structured(content.get("recovery_policy"), out, evidence)
    if "raw_trajectory" in content:
        _from_raw(content.get("raw_trajectory"), out, evidence)
    for text in _texts(content):
        _from_text(text, out, evidence)
    return out, evidence


__all__ = ["PARAM_KEYS", "parse_recovery_params"]
