"""The JSON schema sent with every call (SPEC 9.2). It carries types and enums only: the Claude CLI was
measured on 19 Oct 2026 silently dropping list items that broke a maximum or a maxLength, so the limits
stay in the pydantic model, which checks the answer, and out of the schema, which shapes it."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

DROPPED = frozenset(
    {
        "title", "default", "examples", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        "minLength", "maxLength", "pattern", "minItems", "maxItems", "format",
        "uniqueItems", "multipleOf", "minProperties", "maxProperties",
    }
)  # fmt: skip


def schema_for(model_cls: type[BaseModel]) -> dict:
    """The model's schema with every ``$ref`` written out, no constraints, and every object closed with
    all of its keys required."""
    raw = model_cls.model_json_schema()
    defs = raw.pop("$defs", {})
    return _clean(raw, defs)


def _clean(node: Any, defs: dict) -> Any:
    if isinstance(node, list):
        return [_clean(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:  # the definition, with the keys written next to the reference on top
        name = node["$ref"].rsplit("/", 1)[-1]
        node = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
    out = {}
    for key, value in node.items():
        if key in DROPPED:
            continue
        if key == "properties":  # field names, each with its schema; a field may be called "title"
            out[key] = {name: _clean(sub, defs) for name, sub in value.items()}
        else:
            out[key] = _clean(value, defs)
    if "properties" in out:
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out
