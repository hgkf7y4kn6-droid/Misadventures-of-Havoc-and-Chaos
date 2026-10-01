"""Turn Pydantic JSON schemas into the strict subset structured-output APIs accept."""

from __future__ import annotations

import copy
from typing import Any

_DROP = {"title", "default", "maxLength", "minLength", "maxItems", "minItems", "maximum", "minimum", "pattern", "examples"}


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {k: resolve(v) for k, v in node.items() if k not in _DROP}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"].keys())
            elif out.get("type") == "object":
                # free-form maps (dict[str, X]) are expressed as lists of pairs by our schemas;
                # keep them permissive for providers that allow it.
                out.pop("additionalProperties", None)
            return out
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
