"""Shared JSON Schema validation for persisted BrandLab state."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

class SchemaValidationError(ValueError):
    """Internal schema failure; public boundaries convert this to BrandLabError."""


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"

_REPAIR_HINTS = {
    "required": "Add the missing field, or recreate the file with the owning workflow.",
    "type": "Change the value to the expected type.",
    "enum": "Use one of the allowed values.",
    "const": "Set the field to the required constant.",
    "minimum": "Use a value that is not below the minimum.",
    "maximum": "Use a value that is not above the maximum.",
    "minLength": "Use a non-empty value.",
    "pattern": "Match the required format.",
    "additionalProperties": "Remove the unsupported field, or update the owning workflow.",
}


@lru_cache(maxsize=None)
def _load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / f"{name}.schema.json"
    try:
        with path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
    except FileNotFoundError as exc:
        raise SchemaValidationError(f"[SCHEMA_INVALID] {path}: schema contract is missing. Restore it from the BrandLab repository.") from exc
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(f"[SCHEMA_INVALID] {path}: invalid schema JSON. Restore the contract from the BrandLab repository.") from exc
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return schema


def _json_path(error: jsonschema.ValidationError) -> str:
    parts = [str(part) for part in error.absolute_path]
    return "$" + "".join(f"[{part}]" if part.isdigit() else f".{part}" for part in parts)


def schema_errors(name: str, value: Any) -> list[str]:
    validator = jsonschema.Draft202012Validator(_load_schema(name))
    messages: list[str] = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path)):
        field = error.json_path if hasattr(error, "json_path") else _json_path(error)
        reason = error.message
        hint = _REPAIR_HINTS.get(error.validator, "Review the schema contract and fix the value.")
        messages.append(f"{field}: {reason}. Fix: {hint}")
    return messages


def validate_schema(name: str, value: Any, path: Path | str) -> None:
    errors = schema_errors(name, value)
    if errors:
        details = "; ".join(errors)
        raise SchemaValidationError(f"[SCHEMA_INVALID] {path}: {details}")


def validate_row_schema(name: str, row: Any, path: Path | str, line_no: int) -> None:
    """Validate one collection row against the collection schema's items contract."""
    schema = _load_schema(name)
    item_schema = schema.get("items", {}) if schema.get("type") == "array" else schema
    validator = jsonschema.Draft202012Validator(item_schema)
    errors = []
    for error in sorted(validator.iter_errors(row), key=lambda item: list(item.absolute_path)):
        field = error.json_path if hasattr(error, "json_path") else _json_path(error)
        hint = _REPAIR_HINTS.get(error.validator, "Review the schema contract and fix the value.")
        errors.append(f"{field}: {error.message}. Fix: {hint}")
    if errors:
        raise SchemaValidationError(f"[SCHEMA_INVALID] {path}:{line_no}: " + "; ".join(errors))


def validate_file_schema(name: str, path: Path) -> Any:
    from brandlab_lib import load_yaml, read_json
    value = load_yaml(path) if path.suffix.lower() in {".yaml", ".yml"} else read_json(path)
    validate_schema(name, value, path)
    return value


def resolve_inside(root: Path, relative: Path | str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise SchemaValidationError(f"[PATH_INVALID] {relative}: path must be relative to {root}. Remove the absolute path and retry.")
    resolved = (root / relative_path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SchemaValidationError(f"[PATH_INVALID] {relative}: path escapes {root}. Use a path inside this directory.") from exc
    return resolved


def resolve_job_path(job_dir: Path, relative: Path | str) -> Path:
    return resolve_inside(job_dir, relative)
