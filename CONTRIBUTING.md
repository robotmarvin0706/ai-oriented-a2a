# Contributing

This repository defines a **minimal** peer protocol (v0.3). Prefer fixes that make independent implementations interoperable.

## What to open

- **Interop defects**: use the Interop defect issue template. Include a minimal payload and a `SPEC.md` section reference. When reporting defects, specify the protocol version.
- **Docs / examples**: typos, missing examples, broken links — use the General issue template or a small PR.
- **Design expansions** (full task lifecycle, streaming, enterprise defaults): out of scope for v0.3; open an issue only if you can show a concrete gap in the *current* minimal subset.

## Pull requests

1. Keep PRs small and reviewable.
2. If you change message shapes, update `SPEC.md` and add or adjust files under `examples/`.
3. Do not commit secrets, tokens, or private endpoints.

## Datasets

Packets under `datasets/` are a public corpus and follow [docs/DATA_POLICY.md](docs/DATA_POLICY.md):

- Redact credentials, private paths, user identifiers, raw tool payloads, and environment details.
- Synthetic packets use the `synthetic:` prefix in `provenance.source_ref` and state no numeric uplift (no ΔSR, no "+0.2", no percentages).
- Every packet carries `verification_claim.expected_effect` and `verification_claim.replay_hint`; `provenance.chain` keeps at most 3 hops.
- Data under `datasets/` is contributed under CC0 1.0 (see `datasets/LICENSE`), separate from the MIT-licensed code.
- `tests/interop/test_datasets.py` enforces these checks in CI.

## Secrets

This repo needs **no** GitHub Actions secrets for the current `validate` workflow. Do not add unused secret names "for later" without documenting why.

## Local check

```bash
python3 -c "import json; json.load(open('examples/experience-packet.json')); print('ok')"
pip install -e ".[schema]" pytest referencing -q && pytest tests/interop/ -q
```

`pip install -e .` is enough if you do not need schema tests. The package has no required dependencies; `jsonschema` is only the optional `schema` extra.
