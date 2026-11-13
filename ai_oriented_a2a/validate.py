"""Optional JSON Schema validation. Requires the ``schema`` extra."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA_KINDS = {
    "absorption-result": "absorption-result.schema.json",
    "agent-profile": "agent-profile.schema.json",
    "experience-packet": "experience-packet.schema.json",
    "negotiation": "negotiation.schema.json",
}


class SchemaDependencyError(ImportError):
    """Raised when validate() is called but jsonschema is not installed."""


def schema_dir() -> Path:
    """Return the directory that holds the published ``*.schema.json`` files.

    Prefers package data (a built wheel or ``pip install .``). Falls back to
    the repository's top-level ``schema/`` directory for an editable or
    source checkout where the files were not copied into the package.
    """
    here = Path(__file__).resolve().parent
    bundled = here / "schema"
    if bundled.is_dir() and any(bundled.glob("*.schema.json")):
        return bundled
    repo = here.parent / "schema"
    if repo.is_dir() and any(repo.glob("*.schema.json")):
        return repo
    raise FileNotFoundError(
        "Could not find schema JSON files (looked in package data and "
        "the repository schema/ directory)."
    )


def validate(msg: Mapping[str, Any], kind: str) -> None:
    """Validate ``msg`` against a published schema.

    ``kind`` is one of: absorption-result, agent-profile,
    experience-packet, negotiation.

    This function performs full schema validation only. It does not
    implement the protocol version check, and its behaviour is
    unchanged: an unknown version such as ``"v0.3"`` fails the schema
    ``version`` pattern and raises ``jsonschema.ValidationError``. It
    does not return a rejected Absorption Result.

    Receivers MUST call ``check_incoming`` (or ``version_rejection``)
    first and, on an unknown version, return that rejected Absorption
    Result (``status=rejected``, ``reason`` containing
    ``unsupported_version``). Call ``validate`` only for messages that
    have already passed the version check. See docs/TRANSPORT.md
    ("Version check before schema validation").

    Requires ``pip install 'ai-oriented-a2a[schema]'``. Raises
    ``SchemaDependencyError`` if jsonschema is not installed,
    ``ValueError`` for an unknown kind, and
    ``jsonschema.ValidationError`` if the message is invalid.
    """
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError as exc:
        raise SchemaDependencyError(
            "Schema validation requires the optional extra: "
            "pip install 'ai-oriented-a2a[schema]'"
        ) from exc

    if kind not in SCHEMA_KINDS:
        raise ValueError(
            f"Unknown schema kind {kind!r}. Expected one of: "
            f"{', '.join(sorted(SCHEMA_KINDS))}"
        )

    directory = schema_dir()
    resources = []
    for path in directory.glob("*.schema.json"):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(contents)
        resources.append((path.name, resource))
        schema_id = contents.get("$id")
        if schema_id:
            resources.append((schema_id, resource))
    registry = Registry().with_resources(resources)

    schema_path = directory / SCHEMA_KINDS[kind]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, registry=registry).validate(msg)
