"""Stopping sessions and tasks of any kind (restore, rescan, automation, ...). Jobs: vbr-mcp `stop_job`."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastmcp import Context

from vbr_write_mcp.annotations import tool
from vbr_write_mcp.safety import Level, Request, name_of, run, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp


@mcp.tool(**tool("sessions", "destructive"))
async def stop_session(
    session_id: UUID, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Stop a running session of any type: a restore, Instant Recovery, rescan, health check, SureBackup, malware
    scan, move/copy, etc. (for a backup job run, vbr-mcp `stop_job` gives graceful-stop control).

    What is already done is not rolled back; a stopped restore can leave a partially restored VM or files behind.
    """
    args = tool_args(locals())
    session = await try_get(f"/api/v1/sessions/{session_id}")
    return await run(
        ctx,
        "stop_session",
        Level.DESTRUCTIVE,
        f"Stop session {name_of(session, session_id)}",
        Request("POST", f"/api/v1/sessions/{session_id}/stop"),
        args=args,
        confirm_token=confirm_token,
        preview={"session": summarize(session)},
        impact=["The operation is interrupted; work already done is not rolled back."],
    )


@mcp.tool(**tool("sessions", "destructive"))
async def stop_task(task_id: UUID, ctx: Context | None = None, confirm_token: str | None = None) -> dict[str, Any]:
    """Stop one task (`/api/v1/tasks/{id}`), e.g. a long-running export, deployment or recovery-media task."""
    args = tool_args(locals())
    task = await try_get(f"/api/v1/tasks/{task_id}")
    return await run(
        ctx,
        "stop_task",
        Level.DESTRUCTIVE,
        f"Stop task {name_of(task, task_id)}",
        Request("POST", f"/api/v1/tasks/{task_id}/stop"),
        args=args,
        confirm_token=confirm_token,
        preview={"task": summarize(task)},
    )


@mcp.tool(**tool("sessions", "destructive"))
async def stop_automation_session(
    session_id: UUID, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Stop an automation (import/export) session started by `import_configuration` or `export_configuration`."""
    args = tool_args(locals())
    return await run(
        ctx,
        "stop_automation_session",
        Level.DESTRUCTIVE,
        f"Stop automation session {session_id}",
        Request("POST", f"/api/v1/automation/sessions/{session_id}/stop"),
        args=args,
        confirm_token=confirm_token,
    )
