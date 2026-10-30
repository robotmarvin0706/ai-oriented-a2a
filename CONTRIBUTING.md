# Contributing

This repository defines a **minimal** peer protocol (v0.1). Prefer fixes that make independent implementations interoperable.

## What to open

- **Interop defects**: use the Interop defect issue template. Include a minimal payload and a `SPEC.md` section reference.
- **Docs / examples**: typos, missing examples, broken links — use the General issue template or a small PR.
- **Design expansions** (full task lifecycle, streaming, enterprise defaults): out of scope for v0.1; open an issue only if you can show a concrete gap in the *current* minimal subset.

## Pull requests

1. Keep PRs small and reviewable.
2. If you change message shapes, update `SPEC.md` and add or adjust files under `examples/`.
3. Do not commit secrets, tokens, or private endpoints.

## Secrets

This repo needs **no** GitHub Actions secrets for the current `validate` workflow. Do not add unused secret names "for later" without documenting why.

## Local check

```bash
python3 -c "import json; json.load(open('examples/experience-packet.json')); print('ok')"
```
