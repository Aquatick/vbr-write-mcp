"""VMware vSphere snapshot replica DR: failover, failback and their commit/undo steps. vbr-mcp has none of these."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from vbr_write_mcp.annotations import READ, tool
from vbr_write_mcp.pagination import fetch_all
from vbr_write_mcp.safety import Level, Request, run, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp, vbr_client

_FAILOVER = {
    "now": ("/api/v1/failover/failoverNow/vSphere/snapshot", "Failover now"),
    "planned": ("/api/v1/failover/plannedFailover/vSphere/snapshot", "Planned failover"),
    "permanent": ("/api/v1/failover/permanentFailover/vSphere/snapshot", "Permanent failover"),
    "undo": ("/api/v1/failover/undoFailover/vSphere/snapshot", "Undo failover"),
}
_FAILOVER_IMPACT = {
    "now": "Replica VMs are powered on at the DR site from the chosen points. Production VMs are NOT shut down: "
    "make sure they are off or isolated to avoid split-brain/duplicate IPs.",
    "planned": "Production VMs are synchronized one last time, shut down, and the replicas take over at the DR "
    "site. Production downtime starts now.",
    "permanent": "The replicas become the production VMs for good: replication jobs stop handling them and the "
    "replica restore points are removed. Cannot be failed back.",
    "undo": "The replica VMs are powered off and all changes made in them since failover are DISCARDED.",
}
_FAILBACK = {
    "start": "/api/v1/failback/failbackToProduction/vSphere/snapshot",
    "commit": "/api/v1/failback/commitFailback/vSphere/snapshot",
    "undo": "/api/v1/failback/undoFailback/vSphere/snapshot",
    "switch_to_production": "/api/v1/failback/switchToProduction/vSphere/snapshot",
    "change_switchover_time": "/api/v1/failback/changeSwitchoverTime/vSphere/snapshot",
}
_FAILBACK_IMPACT = {
    "start": "Changes made in the replicas are synchronized back to production; production VMs are powered off "
    "and replaced during switchover.",
    "commit": "Finalizes failback: the replica returns to normal replication state; failback can no longer be undone.",
    "undo": "Production is switched back to the replica state from before failback.",
    "switch_to_production": "Switches over now: the replica VM is powered off and the production VM takes over.",
    "change_switchover_time": "Changes when the pending failback switches over.",
}


async def _points(replica_point_ids: list[UUID]) -> list[Any]:
    return [summarize(await try_get(f"/api/v1/replicaPoints/{pid}")) for pid in replica_point_ids[:20]]


@mcp.tool(annotations=READ, tags={"replication", "read"})
async def list_replicas(
    name_filter: str | None = None,
    replica_id: UUID | None = None,
    max_points: Annotated[int, Field(ge=1, le=100)] = 5,
) -> Any:
    """List VM replicas, or - with `replica_id` - that replica's restore points (newest first), to pick the
    `replicaPointIds` for failover/failback. Read-only."""
    if replica_id is not None:
        replica = await vbr_client.get(f"/api/v1/replicas/{replica_id}")
        points = await fetch_all(
            vbr_client,
            f"/api/v1/replicas/{replica_id}/replicaPoints",
            max_items=max_points,
            orderColumn="CreationTime",
            orderAsc=False,
        )
        return {"replica": replica, "points": points}
    return await fetch_all(vbr_client, "/api/v1/replicas", nameFilter=name_filter)


@mcp.tool(**tool("replication", "destructive"))
async def failover_replicas(
    mode: Literal["now", "planned", "permanent", "undo"],
    replica_point_ids: list[UUID],
    reason: str | None = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """VMware vSphere replica failover. Pass one replica point (restore point of a replica) per VM.

    - `now`: power the replicas on at the DR site (disaster: production is lost).
    - `planned`: last sync, shut production down, switch to the replicas (migration / expected outage).
    - `permanent`: make the replicas the permanent production VMs (no failback).
    - `undo`: discard the failover - replicas are powered off, their changes lost.
    After `now`/`planned`, finish with `failback_replicas` or `failover_replicas(mode='permanent'|'undo')`.
    """
    args = tool_args(locals())
    if not replica_point_ids:
        raise ToolError("Pass at least one replica point id (list_replicas(replica_id=...) shows them).")
    path, label = _FAILOVER[mode]
    body: dict[str, Any] = {"replicaPointIds": replica_point_ids}
    if reason:
        body["reason"] = reason
    return await run(
        ctx,
        "failover_replicas",
        Level.DESTRUCTIVE,
        f"{label} of {len(replica_point_ids)} replica(s)",
        Request("POST", path, body=body, timeout=120),
        args=args,
        confirm_token=confirm_token,
        impact=[_FAILOVER_IMPACT[mode]],
        preview={"replicaPoints": await _points(replica_point_ids)},
    )


@mcp.tool(**tool("replication", "destructive"))
async def failback_replicas(
    action: Literal["start", "commit", "undo", "switch_to_production", "change_switchover_time"],
    replica_point_ids: list[UUID],
    target: Annotated[
        Literal["OriginalVM", "OriginalLocation", "Customized"] | None,
        Field(description="action='start': where to fail back to."),
    ] = None,
    power_on_target_vm: bool = True,
    switchover: Annotated[
        dict[str, Any] | None,
        Field(description="ReplicaFailbackModeSpec {'type': 'Auto'|'Manual'|'Scheduled', 'scheduledTime': ISO-8601}."),
    ] = None,
    dr_site_proxy_ids: list[UUID] | None = None,
    production_site_proxy_ids: list[UUID] | None = None,
    customized: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "target='Customized': extra ViVMSnapshotReplicaCustomizedFailbackSpec fields - destinationhost, "
                "resourcePool, datastore, folder, network (see describe_model)."
            )
        ),
    ] = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Failback of failed-over vSphere replicas, in steps:

    1. `start` - sync replica changes back to `target` (`OriginalVM`: the original VM, `OriginalLocation`: re-create
       it where it was, `Customized`: elsewhere). `switchover` decides when production takes over (`Auto`,
       `Manual` then `switch_to_production`, or `Scheduled`; change it later with `change_switchover_time`).
    2. `commit` - finalize; or `undo` - return to the failed-over state.
    """
    args = tool_args(locals())
    body: dict[str, Any] = {"replicaPointIds": replica_point_ids}
    if action == "start":
        if target is None:
            raise ToolError("action='start' needs `target`.")
        body |= {"type": target, "powerOnTargetVM": power_on_target_vm}
        if dr_site_proxy_ids:
            body["drSiteProxyId"] = dr_site_proxy_ids
        if production_site_proxy_ids:
            body["productionSiteProxyId"] = production_site_proxy_ids
        if target == "Customized":
            if not customized:
                raise ToolError("target='Customized' needs `customized` placement fields.")
            body |= customized
    if switchover and action in ("start", "change_switchover_time"):
        body["failbackMode"] = switchover
    return await run(
        ctx,
        "failback_replicas",
        Level.DESTRUCTIVE,
        f"Failback {action.replace('_', ' ')} for {len(replica_point_ids)} replica(s)",
        Request("POST", _FAILBACK[action], body=body, timeout=120),
        args=args,
        confirm_token=confirm_token,
        impact=[_FAILBACK_IMPACT[action]],
        preview={"replicaPoints": await _points(replica_point_ids)},
    )


@mcp.tool(**tool("replication", "write"))
async def rescan_replicas(
    replica_ids: list[UUID] | None = None, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Rescan replicas to refresh their state in VBR (all replicas when `replica_ids` is omitted)."""
    args = tool_args(locals())
    body = {"replicaIds": replica_ids} if replica_ids else {}
    return await run(
        ctx,
        "rescan_replicas",
        Level.WRITE,
        f"Rescan {len(replica_ids) if replica_ids else 'all'} replica(s)",
        Request("POST", "/api/v1/replicas/rescan", body=body),
        args=args,
        confirm_token=confirm_token,
    )
