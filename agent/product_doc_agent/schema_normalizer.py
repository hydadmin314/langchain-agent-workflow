from __future__ import annotations

import re
from typing import Any

from schema.schema import get_module_json_schema


def normalize_to_module_schema(module_name: str, value: Any) -> Any:
    """Align an LLM module response to the module schema without adding facts."""
    schema = get_module_json_schema(module_name)
    unwrapped = unwrap_module_payload(module_name, value, schema)
    return normalize_value(unwrapped, schema)


def unwrap_module_payload(module_name: str, value: Any, schema: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    if module_name not in value:
        return value

    inner = value[module_name]
    expected_type = schema.get("type")
    if expected_type == "array" and isinstance(inner, list):
        return inner
    if expected_type == "object" and isinstance(inner, dict):
        return inner
    return value


def normalize_value(value: Any, schema: dict[str, Any]) -> Any:
    expected_type = schema.get("type")
    effective_type = choose_type(expected_type, value)

    if effective_type == "object":
        normalized = normalize_object(value, schema)
    elif effective_type == "array":
        normalized = normalize_array(value, schema)
    elif effective_type == "string":
        normalized = "" if value is None else str(value)
    elif effective_type == "number":
        normalized = normalize_number(value, default=0.8 if is_confidence_schema(schema) else 0.0)
    elif effective_type == "integer":
        normalized = normalize_integer(value)
    elif effective_type == "boolean":
        normalized = value if isinstance(value, bool) else False
    elif effective_type == "null":
        normalized = None
    else:
        normalized = value

    return normalize_enum(normalized, schema)


def normalize_object(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}

    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or not properties:
        return dict(value)

    result: dict[str, Any] = {}
    required = schema.get("required", [])
    allowed_keys = set(properties)

    for key, child_value in value.items():
        if key in properties:
            result[key] = normalize_value(child_value, properties[key])
        elif schema.get("additionalProperties") is not False:
            result[key] = child_value

    for key in required:
        if key not in result and key in properties:
            result[key] = default_for_schema(properties[key])

    if schema.get("additionalProperties") is False:
        return {key: result[key] for key in result if key in allowed_keys}
    return result


def normalize_array(value: Any, schema: dict[str, Any]) -> list[Any]:
    if value is None:
        items: list[Any] = []
    elif isinstance(value, list):
        items = value
    else:
        items = [value]

    item_schema = schema.get("items")
    if not isinstance(item_schema, dict):
        return items
    return [normalize_value(item, item_schema) for item in items]


def default_for_schema(schema: dict[str, Any]) -> Any:
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        non_null_types = [item for item in expected_type if item != "null"]
        if not non_null_types:
            return None
        if "boolean" in non_null_types:
            return None
        if "number" in non_null_types or "integer" in non_null_types:
            return None
        expected_type = non_null_types[0]

    if expected_type == "object":
        return normalize_object({}, schema)
    if expected_type == "array":
        return []
    if expected_type == "string":
        return ""
    if expected_type == "number":
        return 0.8 if is_confidence_schema(schema) else 0.0
    if expected_type == "integer":
        return 0
    if expected_type == "boolean":
        return False
    return None


def choose_type(expected_type: Any, value: Any) -> str | None:
    if isinstance(expected_type, str):
        return expected_type
    if not isinstance(expected_type, list):
        return None

    for item in expected_type:
        if matches_type(value, item):
            return item

    non_null_types = [item for item in expected_type if item != "null"]
    if isinstance(value, str):
        if "number" in non_null_types and re.search(r"-?\d+(?:\.\d+)?", value):
            return "number"
        if "integer" in non_null_types and re.search(r"-?\d+", value):
            return "integer"
    if value is None and "null" in expected_type:
        return "null"
    if "null" in expected_type:
        return "null"
    return non_null_types[0] if non_null_types else "null"


def matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "null":
        return value is None
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return True


def normalize_number(value: Any, *, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return default


def normalize_integer(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group(0))
    return 0


def is_confidence_schema(schema: dict[str, Any]) -> bool:
    description = str(schema.get("description", "")).lower()
    return "confidence" in description or "置信" in description


def normalize_enum(value: Any, schema: dict[str, Any]) -> Any:
    enum_values = schema.get("enum")
    if not isinstance(enum_values, list) or value in enum_values:
        return value
    if "" in enum_values:
        return ""
    return enum_values[0] if enum_values else value
