"""Helpers every write workflow needs, plus `api_write`, the escape hatch for any write endpoint."""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any, Literal
from uuid import UUID

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from vbr_write_mcp import catalog
from vbr_write_mcp.annotations import READ, tool
from vbr_write_mcp.safety import Level, Plan, Request, execute, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp, vbr_client
from vbr_write_mcp.settings import write_settings

_SEGMENT = re.compile(r"^[^/?#\\]+$")

# Never reachable through api_write: they would hijack or end this server's own VBR session.
_BUILTIN_BLOCKED = (r"^/api/oauth2/", r"^/api/v1/registerVbr$")

_TERMINAL_STATES = {"Stopped"}


def validate_path(path: str) -> str:
    if not path.startswith("/api/v1/"):
        raise ToolError(f"Path must start with /api/v1/, got {path!r}.")
    segments = path.removeprefix("/").split("/")
    if any(not _SEGMENT.match(s) or s in {".", ".."} for s in segments):
        raise ToolError(
            f"Invalid path {path!r}: pass a plain endpoint path with ids filled in (no '..', empty segments, '?' or "
            "'#'); put query parameters in `params`."
        )
    return path


@mcp.tool(annotations=READ, tags={"core", "read"})
async def describe_endpoint(
    query: Annotated[
        str,
        Field(
            description=(
                "Either an API path (`/api/v1/jobs/{id}` or a concrete one) together with `method`, or search words "
                "such as 'quick backup', 'failover', 'add repository'."
            )
        ),
    ],
    method: Literal["GET", "POST", "PUT", "DELETE"] | None = None,
    depth: Annotated[int, Field(ge=0, le=4)] = 2,
) -> Any:
    """Look up the VBR REST API 1.3-rev2 reference bundled with this server.

    With a path and `method`: the endpoint's summary, description, path/query parameters and request-body schema
    (nested models inlined to `depth`; deeper ones appear as `{"$ref": Model}` - expand them with `describe_model`).
    A model with `x-subtypes` is polymorphic: its `type` field selects one of the listed subtypes, whose extra
    fields the body must carry. With search words: matching endpoints (method, path, summary).
    """
    if method and query.startswith("/api/"):
        entry = catalog.match(method, query)
        if entry is None:
            hits = catalog.search(query.rsplit("/", 1)[-1], method=None, limit=10)
            raise ToolError(f"No {method} {query} in the VBR 1.3-rev2 API. Similar endpoints: {hits}")
        return catalog.describe(entry, depth)
    hits = catalog.search(query, method=method)
    if not hits:
        raise ToolError(f"No endpoint matches {query!r}. Try fewer or different words (e.g. a resource name).")
    return hits


@mcp.tool(annotations=READ, tags={"core", "read"})
async def describe_model(name: str, depth: Annotated[int, Field(ge=0, le=4)] = 2) -> Any:
    """Schema of one VBR API model by name (e.g. `EntireCloudDirectorVMCustomizedRestoreSpec`, `VmwareObjectModel`).

    Shows fields, which are required, enum values and descriptions; `x-subtypes` lists the concrete variants of a
    polymorphic model.
    """
    if name not in catalog.models():
        close = [m for m in catalog.models() if name.lower() in m.lower()][:20]
        raise ToolError(f"Unknown model {name!r}. Models with a similar name: {close}")
    return catalog.expand({"$ref": name}, depth + 1)


@mcp.tool(annotations=READ, tags={"core", "read"})
async def api_read(path: str, params: dict[str, Any] | None = None) -> Any:
    """Send a GET request to the VBR REST API (read-only), e.g. to find ids or check state after an action.

    `path` has its ids filled in (e.g. `/api/v1/jobs/{id}`); `params` holds query parameters (filters,
    `skip`/`limit`, `orderColumn`/`orderAsc`). If Veeam's vbr-mcp is connected too, its dedicated list tools give
    richer answers.
    """
    return await vbr_client.get(validate_path(path), params=params)


@mcp.tool(annotations=READ, tags={"core", "read"})
async def get_session_result(
    session_id: UUID,
    wait_seconds: Annotated[int, Field(ge=0, le=900, description="Poll until the session stops, up to this.")] = 0,
    include_logs: bool = True,
) -> dict[str, Any]:
    """State and result of a VBR session - the asynchronous run started by almost every action tool.

    With `wait_seconds`, polls every 5 s until the session reaches `Stopped` or the time is up. When the session
    stopped with a `Warning` or `Failed` result (or `include_logs` and it is still running), the session log records
    are included so the reason is visible.
    """
    path = f"/api/v1/sessions/{session_id}"
    session = await vbr_client.get(path)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while session.get("state") not in _TERMINAL_STATES and loop.time() < deadline:
        await asyncio.sleep(min(5.0, max(0.0, deadline - loop.time())))
        session = await vbr_client.get(path)
    out: dict[str, Any] = {
        "id": session.get("id"),
        "name": session.get("name"),
        "sessionType": session.get("sessionType"),
        "state": session.get("state"),
        "result": session.get("result"),
        "progressPercent": session.get("progressPercent"),
        "creationTime": session.get("creationTime"),
        "endTime": session.get("endTime"),
        "finished": session.get("state") in _TERMINAL_STATES,
    }
    result = (session.get("result") or {}).get("result")
    if include_logs and (result in ("Warning", "Failed") or session.get("state") not in _TERMINAL_STATES):
        try:
            logs = await vbr_client.get(f"{path}/logs")
            records = logs.get("records", logs) if isinstance(logs, dict) else logs
            out["logs"] = records[-40:] if isinstance(records, list) else records
        except ToolError as e:
            out["logs"] = f"unavailable: {e}"
    return out


@mcp.tool(**tool("generic", "destructive"))
async def api_write(
    method: Literal["POST", "PUT", "DELETE"],
    path: str,
    body: Any = None,
    params: dict[str, Any] | None = None,
    reason: str | None = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Send any state-changing request to the VBR REST API - for write endpoints no dedicated tool covers.

    Examples: High Availability cluster switchover, Azure / file-share / FCD Instant Recovery, Entra ID restores,
    disk publishing (Data Integration API), helper appliances, KMS servers. Look the endpoint up with
    `describe_endpoint` first and build `body` from its schema. Only endpoints of the VBR 1.3-rev2 API are accepted.
    Always treated as destructive: the server asks for confirmation before sending. `reason` is recorded in the
    audit log.
    """
    args = tool_args(locals())
    validate_path(path)
    for pattern in (*_BUILTIN_BLOCKED, *write_settings.blocked_paths):
        if re.search(pattern, path):
            raise ToolError(f"{method} {path} is blocked on this server (pattern {pattern!r}).")
    entry = catalog.match(method, path)
    if entry is None:
        raise ToolError(
            f"{method} {path} is not an endpoint of the VBR 1.3-rev2 API. Find the right one with describe_endpoint."
        )
    impact = [f"Endpoint: {entry['summary']}. {catalog.clean(entry['description'])[:400]}"]
    if reason:
        impact.append(f"Reason given: {reason}")
    preview = None
    if method in ("PUT", "DELETE") and catalog.match("GET", path):
        preview = summarize(await try_get(path))
    plan = Plan(
        tool="api_write",
        level=Level.DESTRUCTIVE,
        summary=f"{method} {path} - {entry['summary']}",
        requests=[Request(method, path, body=body, params=params)],
        impact=impact,
        preview=preview,
    )
    return await execute(ctx, plan, args=args, confirm_token=confirm_token)
