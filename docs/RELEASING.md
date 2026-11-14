# Releasing and publishing to PyPI

This document is for maintainers. It covers building the `ai-oriented-a2a` package and publishing it to TestPyPI and PyPI. Nothing is published automatically: the publish workflow only runs when a maintainer starts it by hand.

- Distribution name: `ai-oriented-a2a`
- Import name: `ai_oriented_a2a`
- Version source: `__version__` in `ai_oriented_a2a/__init__.py` (read by hatchling)
- Required dependencies: none. Optional extra: `[schema]` (`jsonschema>=4.18`)

## First PyPI upload: 0.3.2

Done. `0.3.2` was published to TestPyPI and PyPI on 2026-11-14, built from tag `v0.3.2`: https://pypi.org/project/ai-oriented-a2a/0.3.2/. `0.3.1` was never uploaded: tag `v0.3.1` predates the packaging metadata and the package code has changed since.

The steps that were followed:

1. Merge the PR that sets `__version__ = "0.3.2"`.
2. Create tag `v0.3.2` (and the GitHub release) on that merge commit.
3. Build from the tag (`git checkout v0.3.2`) and publish with one of the methods below, TestPyPI first.

## Version numbers can never be reused

PyPI and TestPyPI accept each version of a file only once. A deleted release cannot be uploaded again under the same version. If an upload is wrong, bump the version (for example `0.3.3`) and publish again. Always publish to TestPyPI first.

## Pre-flight checklist

- [ ] `main` is green (`docs-and-examples`).
- [ ] `__version__` in `ai_oriented_a2a/__init__.py` is the version you intend to publish, and that version is not already on the target index.
- [ ] `CHANGELOG.md` has a section for this version.
- [ ] A clean local build passes:

  ```bash
  rm -rf dist/
  python -m pip install --upgrade build twine
  python -m build
  twine check --strict dist/*
  ```

- [ ] The wheel contains only `ai_oriented_a2a/` (including `ai_oriented_a2a/schema/`) plus `.dist-info`. No `datasets/`, `examples/`, or `tests/`:

  ```bash
  python -m zipfile -l dist/ai_oriented_a2a-*.whl
  tar -tzf dist/ai_oriented_a2a-*.tar.gz
  ```

- [ ] Publishing credentials are set up (next section).

## Credentials

### Option A (recommended): Trusted Publishing

Trusted Publishing lets PyPI trust this repository's workflow through OIDC. No token is stored anywhere.

Add a pending publisher on **both** indexes. Before the first upload the project does not exist yet, so use "Add a new pending publisher":

- PyPI: <https://pypi.org/manage/account/publishing/>
- TestPyPI: <https://test.pypi.org/manage/account/publishing/>

Use these values on both:

| Field | Value |
| --- | --- |
| PyPI project name | `ai-oriented-a2a` |
| Owner | `robotmarvin0706` |
| Repository name | `ai-oriented-a2a` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

Then, in the GitHub repository, go to Settings → Environments and create an environment named `pypi`. Add yourself as a required reviewer so each publish waits for manual approval.

### Option B (fallback): API token

Use this only if Trusted Publishing is not possible.

1. Create a project-scoped API token on PyPI (and separately on TestPyPI if you need it there). Before the first upload, a token can only be account-scoped. Replace it with a project-scoped token after the first upload.
2. Store it yourself as the repository secret `PYPI_API_TOKEN` (Settings → Secrets and variables → Actions). Never paste the value into an issue, PR, commit, or chat.
3. In `.github/workflows/publish.yml`, uncomment the `password: ${{ secrets.PYPI_API_TOKEN }}` line.

A single secret holds one token, and PyPI and TestPyPI tokens are different. If you use the token path for both indexes, update the secret between runs, or use local uploads (Method 2 or 3) for TestPyPI.

## Method 1: GitHub Actions (manual run)

1. Go to Actions → `publish` → "Run workflow", on the release tag (for example `v0.3.2`) or on `main` at the tagged commit.
2. Choose `target: testpypi` and run. Approve the `pypi` environment if prompted.
3. Verify the TestPyPI install (see below).
4. Run the workflow again with `target: pypi`.

The workflow builds the sdist and wheel, runs `twine check --strict`, and uploads with `pypa/gh-action-pypi-publish`.

## Method 2: local `twine upload`

```bash
rm -rf dist/
python -m pip install --upgrade build twine
python -m build
twine check --strict dist/*

# TestPyPI first
twine upload --repository testpypi dist/*

# then PyPI
twine upload dist/*
```

When prompted, the username is `__token__` and the password is your API token. You can also set `TWINE_USERNAME=__token__` and `TWINE_PASSWORD` in your shell. Do not commit a `.pypirc` that contains a token.

## Method 3: local `uv build` + `uv publish`

```bash
rm -rf dist/
uv build

# TestPyPI first
uv publish --publish-url https://test.pypi.org/legacy/ --token "$TEST_PYPI_TOKEN"

# then PyPI
uv publish --token "$PYPI_TOKEN"
```

`uv publish` also reads the token from the `UV_PUBLISH_TOKEN` environment variable.

## Verify the TestPyPI install

Use a fresh virtual environment. `--extra-index-url` lets pip fetch `jsonschema` for the `[schema]` extra from the real PyPI.

```bash
python -m venv /tmp/a2a-check && . /tmp/a2a-check/bin/activate
pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ ai-oriented-a2a==0.3.2
python -c "import ai_oriented_a2a; print(ai_oriented_a2a.__version__)"   # expect 0.3.2

pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "ai-oriented-a2a[schema]==0.3.2"
python -c "import jsonschema, ai_oriented_a2a; print('schema extra ok')"
deactivate
```

After the PyPI upload, repeat the check with plain `pip install ai-oriented-a2a==0.3.2`.

## After publishing

- [ ] Check <https://pypi.org/project/ai-oriented-a2a/>: the version, description, project links, and license are correct.
- [ ] Update `CHANGELOG.md` to record the PyPI publication (for example "Published to PyPI as `ai-oriented-a2a`") in a follow-up PR. Only write this after the upload has actually succeeded.
- [ ] If a token was used, consider revoking an account-scoped token and replacing it with a project-scoped one.
