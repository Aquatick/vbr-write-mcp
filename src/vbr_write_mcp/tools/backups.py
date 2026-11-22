"""Backup lifecycle: delete, move, copy, re-home objects, health check/repair, malware marks on backup files."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from vbr_write_mcp.annotations import tool
from vbr_write_mcp.safety import Level, Request, name_of, run, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp

_LONG = 120.0


@mcp.tool(**tool("backups", "destructive"))
async def delete_backup(
    backup_id: UUID,
    include_gfs: bool = False,
    from_configuration_only: bool = False,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Delete a whole backup (all restore points of every machine in it) from disk.

    `include_gfs=True` also deletes GFS (weekly/monthly/yearly) restore points - without it they are kept.
    `from_configuration_only=True` removes the backup only from the VBR configuration (`fromDB`) and leaves the
    files on the repository (they can be imported again). Immutable restore points cannot be deleted before their
    immutability expires; VBR then reports an error. Irreversible.
    """
    args = tool_args(locals())
    backup = await try_get(f"/api/v1/backups/{backup_id}")
    params: dict[str, Any] = {"includeGFS": include_gfs}
    if from_configuration_only:
        params["fromDB"] = True
    impact = (
        ["Backup is removed from the VBR configuration only; files stay on the repository."]
        if from_configuration_only
        else [
            "All restore points of this backup are deleted from disk - irreversible.",
            "GFS restore points are deleted too." if include_gfs else "GFS restore points are kept.",
        ]
    )
    return await run(
        ctx,
        "delete_backup",
        Level.DESTRUCTIVE,
        f"Delete backup {name_of(backup, backup_id)}",
        Request("DELETE", f"/api/v1/backups/{backup_id}", params=params, timeout=_LONG),
        args=args,
        confirm_token=confirm_token,
        impact=impact,
        preview={"backup": summarize(backup)},
    )


@mcp.tool(**tool("backups", "destructive"))
async def delete_backup_object(
    backup_id: UUID,
    object_id: UUID,
    include_gfs: bool = False,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Delete one machine's restore points from a backup ("Delete from disk" for a single VM/computer).

    `object_id` is the backup object id from vbr-mcp `get_backup_details(include objects)`. Irreversible.
    """
    args = tool_args(locals())
    obj = await try_get(f"/api/v1/backupObjects/{object_id}")
    return await run(
        ctx,
        "delete_backup_object",
        Level.DESTRUCTIVE,
        f"Delete restore points of {name_of(obj, object_id)} from backup {backup_id}",
        Request(
            "DELETE",
            f"/api/v1/backups/{backup_id}/objects/{object_id}",
            params={"includeGFS": include_gfs},
            timeout=_LONG,
        ),
        args=args,
        confirm_token=confirm_token,
        preview={"object": summarize(obj)},
        impact=["Irreversible.", "GFS restore points are deleted too." if include_gfs else "GFS points are kept."],
    )


@mcp.tool(**tool("backups", "destructive"))
async def move_backup(
    backup_id: UUID, target_repository_id: UUID, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Move a backup (whole chain) to another repository; the job is re-pointed to the target.

    Runs as a move session and can take long; jobs using the backup cannot run meanwhile. If it stops halfway,
    `manage_move_copy_session` decides between retry, forget and undo.
    """
    args = tool_args(locals())
    backup = await try_get(f"/api/v1/backups/{backup_id}")
    repo = await try_get(f"/api/v1/backupInfrastructure/repositories/{target_repository_id}")
    return await run(
        ctx,
        "move_backup",
        Level.DESTRUCTIVE,
        f"Move backup {name_of(backup, backup_id)} to repository {name_of(repo, target_repository_id)}",
        Request("POST", f"/api/v1/backups/{backup_id}/moveTo", body={"targetRepositoryId": target_repository_id}),
        args=args,
        confirm_token=confirm_token,
        preview={"backup": summarize(backup), "target": summarize(repo)},
        impact=["Data is moved and deleted from the source repository; the job's target changes."],
    )


@mcp.tool(**tool("backups", "write"))
async def copy_backups(
    spec: dict[str, Any],
    kind: Literal["machines", "unstructured_data"] = "machines",
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Copy backups to another repository (or, for machine backups, a folder) - the source stays as it is.

    `spec` is the request body: for `unstructured_data` a `CopyBackupSpec` (see `describe_model`); for
    `machines` VBR does not publish the schema - typically `backupIds` and a target repository or folder. Check
    `describe_endpoint('/api/v1/backups/machines/copyTo', 'POST')`.
    """
    args = tool_args(locals())
    path = "/api/v1/backups/machines/copyTo" if kind == "machines" else "/api/v1/backups/unstructuredData/copyTo"
    return await run(
        ctx,
        "copy_backups",
        Level.WRITE,
        f"Copy {kind} backups",
        Request("POST", path, body=spec, timeout=_LONG),
        args=args,
        confirm_token=confirm_token,
        impact=["Uses space on the target repository."],
    )


@mcp.tool(**tool("backups", "destructive"))
async def move_backup_objects_to_job(
    backup_id: UUID,
    target_job_id: UUID,
    backup_object_ids: list[UUID] | None = None,
    move_associated_backup_copies: bool = False,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Re-home machines' backups from one job's backup to another job (e.g. after moving VMs between jobs), so
    the target job continues their chains. Without `backup_object_ids` all objects of the backup move."""
    args = tool_args(locals())
    body: dict[str, Any] = {"targetJobId": target_job_id}
    if backup_object_ids:
        body["backupObjects"] = backup_object_ids
    backup = await try_get(f"/api/v1/backups/{backup_id}")
    job = await try_get(f"/api/v1/jobs/{target_job_id}")
    return await run(
        ctx,
        "move_backup_objects_to_job",
        Level.DESTRUCTIVE,
        f"Move {len(backup_object_ids) if backup_object_ids else 'all'} object(s) of backup "
        f"{name_of(backup, backup_id)} to job {name_of(job, target_job_id)}",
        Request(
            "POST",
            f"/api/v1/backups/{backup_id}/objects/moveTo",
            body=body,
            params={"moveAssociatedBackupCopies": move_associated_backup_copies},
        ),
        args=args,
        confirm_token=confirm_token,
        preview={"backup": summarize(backup), "targetJob": summarize(job)},
    )


@mcp.tool(**tool("backups", "destructive", mixed=True))
async def run_backup_health_check(
    backup_ids: list[UUID] | None = None,
    job_ids: list[UUID] | None = None,
    repair: bool = False,
    repair_options: dict[str, Any] | None = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Run a storage-level health check (CRC/consistency) on backups, optionally repairing what it finds.

    Give `backup_ids` and/or `job_ids`. `repair=True` rewrites corrupted blocks / marks broken points and is
    confirmed as destructive; `repair_options` is a `HealthCheckRepairOptions` object. Returns a session.
    """
    args = tool_args(locals())
    if not backup_ids and not job_ids:
        raise ToolError("Give backup_ids and/or job_ids.")
    body: dict[str, Any] = {"isRepair": repair}
    if backup_ids:
        body["backupIds"] = backup_ids
    if job_ids:
        body["jobIds"] = job_ids
    if repair_options:
        body["repairOptions"] = repair_options
    return await run(
        ctx,
        "run_backup_health_check",
        Level.DESTRUCTIVE if repair else Level.WRITE,
        ("Health check and REPAIR" if repair else "Health check") + " of backups",
        Request("POST", "/api/v1/backups/runHealthcheck", body=body),
        args=args,
        confirm_token=confirm_token,
        impact=[
            "Reads every block of the selected backups: heavy repository I/O while it runs.",
            "Repair modifies backup files." if repair else "",
        ],
    )


@mcp.tool(**tool("backups", "destructive", mixed=True))
async def set_backup_file_malware_state(
    backup_id: UUID,
    backup_file_id: UUID,
    state: Literal["clean", "infected"],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Mark one backup file (restore point file) as Clean or Infected for malware detection.

    Marking `infected` blocks normal restores from it (secure restore only); marking `clean` lifts that, so it is
    confirmed by the server.
    """
    args = tool_args(locals())
    action = "markAsClean" if state == "clean" else "markAsInfected"
    return await run(
        ctx,
        "set_backup_file_malware_state",
        Level.DESTRUCTIVE if state == "clean" else Level.WRITE,
        f"Mark backup file {backup_file_id} of backup {backup_id} as {state}",
        Request("POST", f"/api/v1/backups/{backup_id}/backupFiles/{backup_file_id}/{action}"),
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("backups", "destructive", mixed=True))
async def manage_move_copy_session(
    session_id: UUID,
    action: Literal["Retry", "ForgetFailed", "StopAndUndo"],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Resolve a backup move/copy session that is waiting for a decision: `Retry` the failed part, `ForgetFailed`
    (keep what moved, drop the rest) or `StopAndUndo` (roll back)."""
    args = tool_args(locals())
    session = await try_get(f"/api/v1/sessions/{session_id}")
    return await run(
        ctx,
        "manage_move_copy_session",
        Level.WRITE if action == "Retry" else Level.DESTRUCTIVE,
        f"{action} for move/copy session {name_of(session, session_id)}",
        Request("POST", f"/api/v1/backups/moveCopySessions/{session_id}/manage", body={"action": action}),
        args=args,
        confirm_token=confirm_token,
        preview={"session": summarize(session)},
    )


@mcp.tool(**tool("backups", "write"))
async def create_recovery_media(
    backup_object_id: UUID,
    spec: Annotated[dict[str, Any] | None, Field(description="Optional body; see describe_endpoint.")] = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Create Veeam Recovery Media (bare-metal recovery ISO) for an agent backup object."""
    args = tool_args(locals())
    return await run(
        ctx,
        "create_recovery_media",
        Level.WRITE,
        f"Create recovery media for backup object {backup_object_id}",
        Request("POST", f"/api/v1/backupObjects/{backup_object_id}/createRecoveryMedia", body=spec),
        args=args,
        confirm_token=confirm_token,
    )
