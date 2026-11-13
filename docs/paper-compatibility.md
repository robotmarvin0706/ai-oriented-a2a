# Paper Compatibility Checklist

The project prioritizes practical implementation and landability. This document only ensures the development path remains compatible with later academic paper writing (primary target: AAMAS).

## Compatibility Principles

- Protocol stability comes first; avoid frequent changes to core message formats for paper purposes.
- Experiments stay decoupled from the main implementation; large-scale evaluation is not mandatory.
- Key designs should remain measurable, toggleable, and reproducible.

## Checklist

| Priority | Item | Current Status (v0.3) | Suggested Action | Paper Value |
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
