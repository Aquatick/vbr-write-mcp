"""Patch semantics on top of VBR's full-replace PUT endpoints.

VBR edits (`PUT /api/v1/jobs/{id}` and friends) take the *whole* model. The update tools read the current object,
deep-merge the caller's partial `changes` into it and send the result, so the agent only names what changes.
"""

from __future__ import annotations

import copy
from typing import Any

_MAX_DIFF = 60


def deep_merge(base: Any, changes: Any) -> Any:
    """`changes` merged into a copy of `base`: dicts merge key by key, every other value (lists too) replaces."""
    if isinstance(base, dict) and isinstance(changes, dict):
        out = copy.deepcopy(base)
        for key, value in changes.items():
            out[key] = deep_merge(out[key], value) if key in out else copy.deepcopy(value)
        return out
    return copy.deepcopy(changes)


def diff(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    """Leaf-level differences between two JSON values, as `{"path", "old", "new"}` rows."""
    rows: list[dict[str, Any]] = []
    _diff(old, new, path, rows)
    if len(rows) > _MAX_DIFF:
        rows = [*rows[:_MAX_DIFF], {"path": "...", "old": None, "new": f"{len(rows) - _MAX_DIFF} more change(s)"}]
    return rows


def _diff(old: Any, new: Any, path: str, rows: list[dict[str, Any]]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new), key=str):
            sub = f"{path}.{key}" if path else str(key)
            if key not in old:
                rows.append({"path": sub, "old": "<absent>", "new": new[key]})
            elif key not in new:
                rows.append({"path": sub, "old": old[key], "new": "<absent>"})
            else:
                _diff(old[key], new[key], sub, rows)
        return
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for i, (a, b) in enumerate(zip(old, new, strict=True)):
            _diff(a, b, f"{path}[{i}]", rows)
        return
    if old != new:
        rows.append({"path": path or "<root>", "old": old, "new": new})
