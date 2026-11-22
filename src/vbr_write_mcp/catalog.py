"""Bundled catalog of the VBR REST API 1.3-rev2: every endpoint with its query parameters and request-body schema.

Generated from Veeam's official OpenAPI specification (via the `veeam-br` client models) by
`scripts/build_catalog.py`. Used by `describe_endpoint` / `describe_model` so the agent can build a correct body for
any write, and by `api_write` to refuse endpoints that do not exist.
"""

from __future__ import annotations

import gzip
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

_DATA = Path(__file__).parent / "data" / "vbr_api_1.3-rev2.json.gz"
_TAG_RE = re.compile(r"<[^>]+>")


@cache
def _load() -> dict[str, Any]:
    with gzip.open(_DATA, "rt", encoding="utf-8") as f:
        return json.load(f)


def endpoints() -> list[dict[str, Any]]:
    return _load()["endpoints"]


def models() -> dict[str, Any]:
    return _load()["models"]


def clean(text: str | None) -> str:
    """Strip the HTML the spec embeds in descriptions."""
    if not text:
        return ""
    text = text.replace("&#58;", ":").replace("&quot;", '"')
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _segments(path: str) -> list[str]:
    return path.strip("/").split("/")


def match(method: str, path: str) -> dict[str, Any] | None:
    """The catalog entry for a concrete request (`/api/v1/jobs/<uuid>` matches `/api/v1/jobs/{id}`).

    A literal segment beats a `{param}` segment, so `/api/v1/jobs/quickBackup/vSphere` never matches `{id}` routes.
    """
    method = method.upper()
    want = _segments(path.split("?")[0])
    best: tuple[int, dict[str, Any]] | None = None
    for ep in endpoints():
        if ep["method"] != method:
            continue
        pat = _segments(ep["path"])
        if len(pat) != len(want):
            continue
        score = 0
        for p, w in zip(pat, want, strict=True):
            if p.startswith("{"):
                continue
            if p != w:
                break
            score += 1
        else:
            if best is None or score > best[0]:
                best = (score, ep)
    return best[1] if best else None


def search(query: str, method: str | None = None, limit: int = 25) -> list[dict[str, str]]:
    """Endpoints whose path, summary or operation name contains every word of `query` (case-insensitive)."""
    words = [w.lower() for w in query.split() if w]
    out = []
    for ep in endpoints():
        if method and ep["method"] != method.upper():
            continue
        hay = f"{ep['path']} {ep['summary']} {ep['operation']} {ep['tag']}".lower()
        if all(w in hay for w in words):
            out.append({"method": ep["method"], "path": ep["path"], "summary": ep["summary"], "tag": ep["tag"]})
            if len(out) >= limit:
                break
    return out


def expand(schema: Any, depth: int, _seen: frozenset[str] = frozenset()) -> Any:
    """Inline `{"$ref": Model}` references up to `depth` levels; deeper ones stay as refs for `describe_model`."""
    if isinstance(schema, list):
        return [expand(s, depth, _seen) for s in schema]
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if ref is not None:
        if depth <= 0 or ref in _seen or ref not in models():
            return {"$ref": ref}
        body = models()[ref] or {}
        return {"$model": ref, **expand(body, depth - 1, _seen | {ref})}
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "description":
            out[key] = clean(value)
        elif key in ("properties",):
            out[key] = {k: expand(v, depth, _seen) for k, v in value.items()}
        elif key in ("items",) or key == "oneOf":
            out[key] = expand(value, depth, _seen)
        else:
            out[key] = value
    return out


def describe(entry: dict[str, Any], depth: int) -> dict[str, Any]:
    return {
        "method": entry["method"],
        "path": entry["path"],
        "summary": entry["summary"],
        "description": clean(entry["description"]),
        "pathParams": entry["pathParams"],
        "query": expand(entry["query"], 0),
        "body": expand(entry["body"], depth) if entry["body"] else None,
    }
