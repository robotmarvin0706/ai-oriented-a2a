# Paper Compatibility Checklist

This is an **infrastructure project**. The primary objective is a small, implementable, debuggable protocol that independent agents can exchange over; see the Goals section of [README.md](../README.md).

Academic writing is a **derivative objective**. If a paper is written from this work, its primary target venue is AAMAS. This document exists only to keep that later option open. It adds no wire requirement, changes no schema, and is never a reason to delay a release or to widen scope. When the two objectives conflict, implementation and protocol stability win — that is the whole content of every item below.

Reading note: nothing here describes a current commitment to publish, and no status in this document is evidence of value. The project's success condition is third-party adoption (see the Goals section of the README), not a publication.

## Compatibility Principles

- Protocol stability comes first; do not change core message formats for paper purposes.
- Experiments stay decoupled from the main implementation; large-scale evaluation is not mandatory.
- Key designs should remain measurable, toggleable, and reproducible.

## Checklist

| Priority | Item | Current Status (last reviewed at v0.3) | Suggested Action | Paper Value |
|----------|------|-----------------------|------------------|-------------|
| High | Quantifiable metrics | Net ΔSR skeleton exists | Keep result logs for key versions | Core claim support |
| High | Toggleable modules | Protocol is modular | Make Experience Packet / negotiation / absorption configurable | Ablation foundation |
| High | Reproducibility | Experiment script supports seeds | Save seed + config for important runs | Reviewer trust |
| Medium | Baseline hooks | Not yet present | Reserve interfaces for “no Packet / no negotiation” variants | Comparison experiments |
| Medium | Experiment logs | Partially supported | Standardize JSON result output | Direct table generation |
| Low | Separate notes | None | This file or `docs/experiment-log.md` | Writing efficiency |

## Currently Available

- `SPEC.md`: Protocol specification
- `schema/`: Message formats
- `examples/net_delta_sr_experiment.py`: Stage-1 experiment skeleton with fixed seeds
- `examples/demo_round.py`: Minimal demonstration

## Maintenance

Update the “Current Status” column as the project evolves. Completing all items at once is not required.

The column is currently stale: it was last reviewed at v0.3, and v0.4 added the WP1/WP3 comparison arms, JSON summaries and seed-pinned data, so at least “Baseline hooks”, “Experiment logs” and “Reproducibility” have moved. Re-review it in the next paper-facing PR, or delete the column if the paper option is dropped. This is tracked as debt, not as a blocker.
