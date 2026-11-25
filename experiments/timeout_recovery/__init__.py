"""Timeout-recovery four-arm experiment (non-normative, host-side).

Run: ``python -m experiments.timeout_recovery.run --seeds 42 7 2026 --n 30`` (stdout only;
add ``--results --summary-dir --data-dir`` to regenerate the committed files).
See RESULTS.md in this folder for the latest table.

``python -m experiments.timeout_recovery.wp3 --seeds 42 7 2026 31337 --n 30`` runs
the WP3 comparison (how the same experience reaches the host) and applies the
rules pre-registered in ``run.py``; see WP3_RESULTS.md.
"""
