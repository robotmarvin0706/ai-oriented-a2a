"""Parity test: the Go ``rank`` subcommand vs the Python selection reference.

The Go implementation (``impls/go``) applies the selection rules of
``docs/PREREG_SELECTION.md`` §7 to a frozen packet pool, with a deterministic
tie-breaker (packet id ascending) in place of the experiment's seeded RNG.
This test checks that it agrees exactly with the Python reference
(``experiments.selection.sim``) on every pool of
``tests/conformance/fixtures/selection_pools.json``, in both modes.

The Go binary is built once per module into a pytest ``tmp_path``; set
``A2A_GO_BIN`` to use an existing binary instead. Without Go on ``PATH`` and
without ``A2A_GO_BIN`` the module is skipped, so a CI job without a Go
toolchain does not fail.

This module does not use ``start_receiver``, so it adds no implementation ids
to the HTTP conformance parametrization.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.selection.sim import claim_eligible, history_score  # noqa: E402
from experiments.selection.pool import source_of  # noqa: E402

GO_DIR = REPO / "impls" / "go"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "selection_pools.json"
MODES = ("history", "claim")


# ---------------------------------------------------------------------------
# Go binary
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def go_bin(tmp_path_factory):
    """Path to the Go binary under test (built once per module)."""
    env_bin = os.environ.get("A2A_GO_BIN")
    if env_bin:
        path = Path(env_bin)
        if not path.is_file():
            pytest.fail(f"A2A_GO_BIN={env_bin!r} is not a file")
        return str(path)
    go = shutil.which("go")
    if go is None:
        pytest.skip("go is not on PATH and A2A_GO_BIN is not set")
    out = tmp_path_factory.mktemp("go-bin") / "a2a-go"
    proc = subprocess.run(
        [go, "build", "-o", str(out), "."],
        cwd=str(GO_DIR),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail(f"go build failed:\n{proc.stdout}\n{proc.stderr}")
    return str(out)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pools():
    doc = json.loads(FIXTURE.read_text())
    assert isinstance(doc.get("pools"), list) and doc["pools"], "fixture has no pools"
    return doc["pools"]


def _pool_ids(pools):
    return [p["name"] for p in pools]


# ---------------------------------------------------------------------------
# Python reference ranking (deterministic tie-breaker: packet id ascending)
# ---------------------------------------------------------------------------


def _own_counts(pool):
    """Build ``{source: [own_absorbed, own_not_absorbed]}`` from the pool.

    §7.2 counts own results "attributed to source s", where s is the
    ``provenance.source_ref`` of the pool packet the result refers to. A
    packet without a non-empty string ``source_ref`` is not attributable to
    any source, so results pointing at it are attributed to NOTHING (they are
    not lumped into an empty-string source) and such a packet's own counts
    stay 0. This mirrors the Go rule in ``impls/go/rank.go`` (``sourceRef``).
    """
    source_by_id = {}
    for packet in pool["packets"]:
        source = source_of(packet)
        if isinstance(source, str) and source:
            source_by_id[packet["id"]] = source
    own = {}
    for entry in pool.get("own_results") or []:
        result = entry.get("result") or {}
        source = source_by_id.get(result.get("packet_ref"))
        if source is None:
            continue  # not in the pool, or not attributable to a source
        status = result.get("status")
        rolled_back = bool(entry.get("rolled_back"))
        counts = own.setdefault(source, [0, 0])
        if status == "absorbed" and not rolled_back:
            counts[0] += 1
        elif status in ("rejected", "isolated") or (status == "absorbed" and rolled_back):
            counts[1] += 1
    return own


def _python_history_ranking(pool):
    own = _own_counts(pool)
    scored = [(history_score(p, own), p["id"]) for p in pool["packets"]]
    # Score descending, ties by packet id ascending (byte-wise).
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [pid for _, pid in scored]


def _python_claim_ranking(pool):
    eligible = []
    ineligible = []
    for packet in pool["packets"]:
        if claim_eligible(packet):
            eligible.append((Fraction(str(packet["verification_claim"]["value"])), packet["id"]))
        else:
            ineligible.append(packet["id"])
    eligible.sort(key=lambda item: (-item[0], item[1]))
    return [pid for _, pid in eligible] + sorted(ineligible)


def _python_ranking(pool, mode):
    return _python_history_ranking(pool) if mode == "history" else _python_claim_ranking(pool)


# ---------------------------------------------------------------------------
# Go ranking
# ---------------------------------------------------------------------------


def _go_ranking(go_bin, pool_name, mode):
    proc = subprocess.run(
        [go_bin, "rank", "--pool", str(FIXTURE), "--mode", mode, "--pool-name", pool_name],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"rank failed: {proc.stdout}\n{proc.stderr}"
    doc = json.loads(proc.stdout)
    assert doc["mode"] == mode
    return doc["ranking"]


# ---------------------------------------------------------------------------
# Parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_go_ranking_matches_python_reference(go_bin, pools, mode):
    for pool in pools:
        expected = _python_ranking(pool, mode)
        got = _go_ranking(go_bin, pool["name"], mode)
        assert got == expected, f"pool {pool['name']!r}, mode {mode!r}: Go {got} != Python {expected}"
        assert sorted(got) == sorted(p["id"] for p in pool["packets"]), (
            f"pool {pool['name']!r}, mode {mode!r}: ranking is not a permutation of the pool"
        )


def test_python_reference_top_choice_matches_select_history(go_bin, pools):
    """Without a tie, the reference's top choice is what ``select_history`` picks."""
    import random

    from experiments.selection.sim import select_history

    checked = 0
    for pool in pools:
        ranking = _python_history_ranking(pool)
        own = _own_counts(pool)
        scores = [history_score(p, own) for p in pool["packets"]]
        best = max(scores)
        if scores.count(best) != 1:
            continue  # a tie: the seeded RNG decides, not the tie-breaker
        index = [p["id"] for p in pool["packets"]].index(ranking[0])
        picked = select_history(
            list(range(len(pool["packets"]))), pool["packets"], own, random.Random(0)
        )
        assert picked == index, f"pool {pool['name']!r}: select_history picked {picked}, ranking top {index}"
        checked += 1
    assert checked >= 1, "no pool without a tie: the sanity check did not run"


# ---------------------------------------------------------------------------
# The fixture must actually exercise the interesting cases
# ---------------------------------------------------------------------------


def test_fixture_has_ties(pools):
    tied_pools = []
    for pool in pools:
        own = _own_counts(pool)
        scores = [history_score(p, own) for p in pool["packets"]]
        if len(scores) != len(set(scores)):
            tied_pools.append(pool["name"])
    assert tied_pools, "no pool has a deliberate history-score tie"


def test_fixture_has_dishonest_peer(pools):
    """A dishonest peer: inflated prior_outcomes or an inflated claim."""
    found = []
    for pool in pools:
        for packet in pool["packets"]:
            prior = packet.get("prior_outcomes") or {}
            absorbed = prior.get("absorbed_count") or 0
            rejected = prior.get("rejected_count") or 0
            claim = packet.get("verification_claim") or {}
            value = claim.get("value")
            if absorbed >= 8 and rejected <= 4:
                found.append((pool["name"], packet["id"], "inflated prior_outcomes"))
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0.85:
                found.append((pool["name"], packet["id"], "inflated claim"))
    assert found, "no dishonest peer (inflated prior_outcomes or claim) in the fixture"


def test_fixture_has_every_kind_of_ineligible_claim(pools):
    kinds = {
        "wrong_metric": lambda c: c.get("metric") != "recovery_sr",
        "n_below_30": lambda c: isinstance(c.get("repro", {}).get("n"), int)
        and not isinstance(c.get("repro", {}).get("n"), bool)
        and c["repro"]["n"] < 30,
        "n_float": lambda c: isinstance(c.get("repro", {}).get("n"), float),
        "n_bool": lambda c: isinstance(c.get("repro", {}).get("n"), bool),
        "missing_value": lambda c: "value" not in c,
        "missing_claim": None,
    }
    seen = set()
    for pool in pools:
        for packet in pool["packets"]:
            claim = packet.get("verification_claim")
            if claim is None:
                seen.add("missing_claim")
                continue
            for name, predicate in kinds.items():
                if predicate is not None and predicate(claim):
                    seen.add(name)
    missing = set(kinds) - seen
    assert not missing, f"fixture lacks ineligible claim kinds: {sorted(missing)}"


def test_fixture_has_eligible_claims(pools):
    eligible = [
        (pool["name"], packet["id"])
        for pool in pools
        for packet in pool["packets"]
        if claim_eligible(packet)
    ]
    assert eligible, "no eligible claim in the fixture"


def test_fixture_has_unattributable_packet_with_own_result(pools):
    """The edge case of §7.2: an own result for a packet with no source_ref."""
    found = False
    for pool in pools:
        unattributable = {
            p["id"] for p in pool["packets"] if not (isinstance(source_of(p), str) and source_of(p))
        }
        for entry in pool.get("own_results") or []:
            if (entry.get("result") or {}).get("packet_ref") in unattributable:
                found = True
    assert found, "fixture has no own result pointing at a packet without provenance.source_ref"
