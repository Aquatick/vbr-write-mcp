"""Job actions vbr-mcp does not have. Create / edit / delete a job: `create_object` / `update_object` /
`delete_object` with kind='job'. Start, stop, retry, enable and disable are in vbr-mcp."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastmcp import Context
from fastmcp.exceptions import ToolError

from vbr_write_mcp.annotations import tool
from vbr_write_mcp.safety import Level, Request, name_of, run, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp

_QUICK_BACKUP_PATHS = {
    "vsphere": "/api/v1/jobs/quickBackup/vSphere",
    "cloud_director": "/api/v1/jobs/quickBackup/vSphere",
    "hyperv": "/api/v1/jobs/quickBackup/hyperV",
    "agent": "/api/v1/jobs/quickBackup/agent",
}


@mcp.tool(**tool("jobs", "write"))
async def clone_job(job_id: UUID, ctx: Context | None = None, confirm_token: str | None = None) -> dict[str, Any]:
    """Clone a job. VBR creates the copy under a derived name; rename or adjust it afterwards with
    `update_object(kind='job', ...)` and check its schedule before it first runs.

    Supported job types: VSphereBackup, HyperVBackup, CloudDirectorBackup, BackupCopy, WindowsAgentBackup,
    LinuxAgentBackup. Returns the new job's model.
    """
    args = tool_args(locals())
    job = await try_get(f"/api/v1/jobs/{job_id}")
    return await run(
        ctx,
        "clone_job",
        Level.WRITE,
        f"Clone job {name_of(job, job_id)}",
        Request("POST", f"/api/v1/jobs/{job_id}/clone"),
        args=args,
        confirm_token=confirm_token,
        preview={"source": summarize(job)},
    )


@mcp.tool(**tool("jobs", "write"))
async def start_quick_backup(
    platform: Literal["vsphere", "cloud_director", "hyperv", "agent"],
    object: dict[str, Any],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Quick Backup: an on-demand incremental of one machine that is already protected by a backup job.

    `object` is the inventory object exactly as VBR returns it: for `vsphere`/`cloud_director` a
    `VmwareObjectModel` (`platform`, `hostName`, `name`, `type: VirtualMachine`, `objectId`) from vbr-mcp
    `browse_inventory`; for `hyperv` a `HyperVObjectModel`; for `agent` an `AgentObjectModel` (`id`, `name`,
    `type`, `protectionGroupId`, `platform: Agent`). The machine must have a backup in a job; VBR uses that job's
    settings and adds a restore point to its chain. Returns a session.
    """
    args = tool_args(locals())
    if not object.get("name"):
        raise ToolError("`object` must be a full inventory object with at least `name` (see browse_inventory).")
    return await run(
        ctx,
        "start_quick_backup",
        Level.WRITE,
        f"Quick backup of {object.get('name')} ({platform})",
        Request("POST", _QUICK_BACKUP_PATHS[platform], body=object),
        args=args,
        confirm_token=confirm_token,
        impact=["Adds an incremental restore point to the existing chain of the job that protects this machine."],
    )


@mcp.tool(**tool("jobs", "write"))
async def apply_job_configuration(
    job_id: UUID, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Push a Veeam Agent backup policy's current configuration to the agents it manages ("Apply configuration").

    Only for agent backup policies (`*AgentBackupServerPolicy` / `*WorkstationPolicy` job types).
    """
    args = tool_args(locals())
    job = await try_get(f"/api/v1/jobs/{job_id}")
    return await run(
        ctx,
        "apply_job_configuration",
        Level.WRITE,
        f"Apply policy configuration of {name_of(job, job_id)}",
        Request("POST", f"/api/v1/jobs/{job_id}/applyConfiguration"),
        args=args,
        confirm_token=confirm_token,
        preview={"job": summarize(job)},
    )


@mcp.tool(**tool("jobs", "destructive"))
async def clear_job_cache(job_id: UUID, ctx: Context | None = None, confirm_token: str | None = None) -> dict[str, Any]:
    """Clear the backup cache of a Veeam Agent backup policy (the local cache agents write to while the repository
    is unreachable). Make sure cached restore points were uploaded first."""
    args = tool_args(locals())
    job = await try_get(f"/api/v1/jobs/{job_id}")
    return await run(
        ctx,
        "clear_job_cache",
        Level.DESTRUCTIVE,
        f"Clear backup cache of {name_of(job, job_id)}",
        Request("POST", f"/api/v1/jobs/{job_id}/clearCache"),
        args=args,
        confirm_token=confirm_token,
        preview={"job": summarize(job)},
        impact=["Restore points held only in the agents' local backup cache may be lost."],
    )
