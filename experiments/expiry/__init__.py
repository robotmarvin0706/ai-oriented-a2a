"""Expiry experiment (experiment 2 of ``docs/PREREG_EXPIRY.md``).

Non-normative. The rules are frozen in ``docs/PREREG_EXPIRY.md`` (merged in
main at ``56a47f3``, file content of ``21d7c93``); this package implements them
on top of ``experiments/timeout_recovery`` without modifying it. The v2 lease
rule of §5.2 lives here, not in ``host.py``.

Run ``python -m experiments.expiry.run --help``.
"""
