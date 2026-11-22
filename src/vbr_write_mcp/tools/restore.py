"""Restores vbr-mcp does not cover: VMware Cloud Director VM restore, Hyper-V full restore and Instant Recovery,
and guest file-level restore (mount, browse, restore, unmount). vSphere VM restore / Instant Recovery, file share
and object storage restores are in vbr-mcp."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from vbr_write_mcp.annotations import READ, tool
from vbr_write_mcp.safety import Level, Request, name_of, run, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp, vbr_client

_LONG = 180.0
_HV_IR = "/api/v1/restore/instantRecovery/hyperV/vm"

SecureRestore = Annotated[
    dict[str, Any] | None,
    Field(
        description=(
            "Optional SecureRestoreSpec: {'antivirusScanEnabled': bool, 'yaraScanEnabled': bool, 'yaraRule': str, "
            "'virusDetectionAction': 'AbortRecovery'|'DisableNetwork'|'Ignore', 'entireVolumeScanEnabled': bool}."
        )
    ),
]


async def _restore_point(restore_point_id: UUID) -> Any:
    return summarize(await try_get(f"/api/v1/restorePoints/{restore_point_id}"))


def _common(body: dict[str, Any], power_up: bool, reason: str | None, secure_restore: dict[str, Any] | None) -> None:
    body["powerUp"] = power_up
    if reason:
        body["reason"] = reason
    if secure_restore:
        body["secureRestore"] = secure_restore


@mcp.tool(**tool("restore", "destructive"))
async def restore_cloud_director_vm(
    restore_point_id: UUID,
    mode: Literal["OriginalLocation", "Customized"],
    new_name: str | None = None,
    vapp: Annotated[
        dict[str, Any] | None,
        Field(description="Customized: target vApp, the inventory object exactly as browse_inventory returns it."),
    ] = None,
    restore_vsphere_vm_tags: bool = False,
    network: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Customized: RestoreTargetNetworkSpec {'networks': [{'sourceNetwork': obj, 'targetNetwork': obj}], "
                "'disconnected': bool}."
            )
        ),
    ] = None,
    datastore: Annotated[
        dict[str, Any] | None,
        Field(
            description="Customized: {'datastore': obj, 'storagePolicy': obj} (CloudDirectorRestoreTargetDatastoreSpec)."
        ),
    ] = None,
    proxy_ids: list[UUID] | None = None,
    power_up: bool = False,
    reason: str | None = None,
    secure_restore: SecureRestore = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Full restore of a VMware Cloud Director VM from a restore point of a Cloud Director backup job.

    `OriginalLocation` restores the VM into its original vApp, **replacing the existing VM** if it is still there.
    `Customized` restores it into `vapp` under `new_name` (required, keeps the original untouched), optionally with
    network mapping and datastore/storage policy. Inventory objects (vApp, networks, datastores) come from vbr-mcp
    `browse_inventory` on the Cloud Director server. `proxy_ids` pins backup proxies (default: automatic).
    Returns a restore session.
    """
    args = tool_args(locals())
    body: dict[str, Any] = {"restorePointId": restore_point_id, "type": mode}
    impact: list[str] = []
    if mode == "Customized":
        if not new_name or not vapp:
            raise ToolError("Customized restore needs `new_name` and the target `vapp` inventory object.")
        body["location"] = {"newName": new_name, "vApp": vapp, "restoreVSphereVMTags": restore_vsphere_vm_tags}
        if network:
            body["network"] = network
        if datastore:
            body["datastore"] = datastore
        impact.append(f"A new VM {new_name!r} is created in vApp {vapp.get('name', '?')}; the original is untouched.")
    else:
        if any(v is not None for v in (new_name, vapp, network, datastore)):
            raise ToolError("new_name / vapp / network / datastore only apply to mode='Customized'.")
        impact.append("Restores to the original vApp: the existing VM with this identity is OVERWRITTEN.")
    if proxy_ids:
        body["restoreProxies"] = {"autoSelectEnabled": False, "proxyIds": proxy_ids}
    _common(body, power_up, reason, secure_restore)
    if power_up:
        impact.append("The VM is powered on after restore - beware of duplicate IPs/hostnames with the original.")
    return await run(
        ctx,
        "restore_cloud_director_vm",
        Level.DESTRUCTIVE,
        f"Restore Cloud Director VM ({mode})",
        Request("POST", "/api/v1/restore/vmRestore/cloudDirector", body=body, timeout=_LONG),
        args=args,
        confirm_token=confirm_token,
        impact=impact,
        preview={"restorePoint": await _restore_point(restore_point_id)},
    )


def _hv_customized(
    body: dict[str, Any],
    destination_host: dict[str, Any] | None,
    new_name: str | None,
    datastore: dict[str, Any] | None,
    network: dict[str, Any] | None,
) -> None:
    if destination_host:
        body["destinationHost"] = destination_host
    if new_name:
        body["target"] = {"vmName": new_name}
    if datastore:
        body["datastore"] = datastore
    if network:
        body["network"] = network


@mcp.tool(**tool("restore", "destructive"))
async def restore_hyperv_vm(
    restore_point_id: UUID,
    mode: Literal["OriginalLocation", "Customized"],
    destination_host: Annotated[
        dict[str, Any] | None, Field(description="Customized: HyperVObjectModel of the target host/cluster.")
    ] = None,
    new_name: str | None = None,
    datastore: Annotated[dict[str, Any] | None, Field(description="Customized: HvRestoreTargetDatastoresSpec.")] = None,
    network: Annotated[dict[str, Any] | None, Field(description="Customized: HvRestoreTargetNetworkSpec.")] = None,
    power_up: bool = False,
    reason: str | None = None,
    secure_restore: SecureRestore = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Full restore of a Microsoft Hyper-V VM. `OriginalLocation` overwrites the VM in place; `Customized` restores
    to `destination_host` (and optionally a new name, storage and network mapping - see `describe_model` for
    `HvRestoreTargetDatastoresSpec` / `HvRestoreTargetNetworkSpec` / `HvRestoreTargetNameSpec`)."""
    args = tool_args(locals())
    body: dict[str, Any] = {"restorePointId": restore_point_id, "type": mode}
    if mode == "Customized":
        if not destination_host:
            raise ToolError("Customized restore needs `destination_host`.")
        _hv_customized(body, destination_host, new_name, datastore, network)
        impact = ["Restores to the chosen host; the original VM is untouched unless it is the same VM/location."]
    else:
        impact = ["Restores to the original location: the existing VM is OVERWRITTEN."]
    _common(body, power_up, reason, secure_restore)
    return await run(
        ctx,
        "restore_hyperv_vm",
        Level.DESTRUCTIVE,
        f"Restore Hyper-V VM ({mode})",
        Request("POST", "/api/v1/restore/vmRestore/hyperV", body=body, timeout=_LONG),
        args=args,
        confirm_token=confirm_token,
        impact=impact,
        preview={"restorePoint": await _restore_point(restore_point_id)},
    )


@mcp.tool(**tool("restore", "destructive"))
async def start_instant_recovery_hyperv_vm(
    restore_point_id: UUID,
    mode: Literal["OriginalLocation", "Customized", "OtherPlatform"],
    destination_host: Annotated[dict[str, Any] | None, Field(description="HyperVObjectModel of the target.")] = None,
    new_name: str | None = None,
    datastore: dict[str, Any] | None = None,
    network: dict[str, Any] | None = None,
    share_credentials_id: UUID | None = None,
    power_up: bool = False,
    reason: str | None = None,
    secure_restore: SecureRestore = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Instant Recovery to Microsoft Hyper-V: run the VM straight from the backup, without a full data copy.

    `OriginalLocation` publishes it in place of the original; `Customized` on `destination_host`; `OtherPlatform`
    recovers a VMware/agent/other backup onto Hyper-V (needs `destination_host`). Finish with
    `finish_instant_recovery_hyperv_vm` (migrate to production or unmount).
    """
    args = tool_args(locals())
    body: dict[str, Any] = {"restorePointId": restore_point_id, "type": mode}
    if mode != "OriginalLocation":
        if not destination_host:
            raise ToolError(f"mode={mode} needs `destination_host`.")
        _hv_customized(body, destination_host, new_name, datastore, network)
    if share_credentials_id:
        body["shareCredentialsId"] = share_credentials_id
    _common(body, power_up, reason, secure_restore)
    return await run(
        ctx,
        "start_instant_recovery_hyperv_vm",
        Level.DESTRUCTIVE,
        f"Instant Recovery to Hyper-V ({mode})",
        Request("POST", _HV_IR, body=body, timeout=_LONG),
        args=args,
        confirm_token=confirm_token,
        preview={"restorePoint": await _restore_point(restore_point_id)},
        impact=[
            "A VM running from the backup repository is published to production Hyper-V.",
            "OriginalLocation replaces the original VM." if mode == "OriginalLocation" else "",
        ],
    )


@mcp.tool(**tool("restore", "destructive"))
async def finish_instant_recovery_hyperv_vm(
    mount_id: UUID,
    action: Literal["migrate", "unmount"],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Finish a Hyper-V Instant Recovery mount: `migrate` the running VM to production storage, or `unmount`
    (tear it down; changes made inside the recovered VM are lost). Mount ids: `api_read('/api/v1/restore/
    instantRecovery/hyperV/vm')`."""
    args = tool_args(locals())
    mount = await try_get(f"{_HV_IR}/{mount_id}")
    return await run(
        ctx,
        "finish_instant_recovery_hyperv_vm",
        Level.DESTRUCTIVE,
        f"{action.capitalize()} Hyper-V Instant Recovery {name_of(mount, mount_id)}",
        Request("POST", f"{_HV_IR}/{mount_id}/{action}", timeout=_LONG),
        args=args,
        confirm_token=confirm_token,
        preview={"mount": summarize(mount)},
        impact=[
            "Changes made in the recovered VM are discarded."
            if action == "unmount"
            else "The VM is moved to production storage; the mount ends when migration completes."
        ],
    )


# --- guest file-level restore ------------------------------------------------------------------------------------


@mcp.tool(**tool("restore", "write"))
async def start_file_level_restore(
    restore_point_id: UUID,
    guest_os: Literal["Windows", "Linux"],
    auto_unmount_minutes: Annotated[int | None, Field(ge=5, description="Unmount after this much inactivity.")] = 30,
    reason: str | None = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Mount a machine restore point for guest file-level restore (FLR). Returns the mount session; its id is the
    `session_id` for `browse_file_level_restore`, `restore_files` and `unmount_file_level_restore`.

    Mounting changes nothing in production. Linux FLR uses a helper appliance/host configured in VBR.
    """
    args = tool_args(locals())
    body: dict[str, Any] = {
        "restorePointId": restore_point_id,
        "type": guest_os,
        "autoUnmount": {"isEnabled": auto_unmount_minutes is not None}
        | ({"noActivityPeriodInMinutes": auto_unmount_minutes} if auto_unmount_minutes else {}),
    }
    if reason:
        body["reason"] = reason
    return await run(
        ctx,
        "start_file_level_restore",
        Level.WRITE,
        f"Mount restore point for {guest_os} file-level restore",
        Request("POST", "/api/v1/restore/flr", body=body, timeout=_LONG),
        args=args,
        confirm_token=confirm_token,
        preview={"restorePoint": await _restore_point(restore_point_id)},
    )


@mcp.tool(annotations=READ, tags={"restore", "read"})
async def browse_file_level_restore(
    session_id: UUID,
    path: Annotated[str, Field(description="Folder in the mounted guest, e.g. 'C:\\\\' or '/etc'.")],
    search: Annotated[str | None, Field(description="If set, search below `path` for this name pattern.")] = None,
    item_types: list[Literal["Drive", "File", "Folder", "Link"]] | None = None,
    limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    skip: Annotated[int, Field(ge=0)] = 0,
) -> Any:
    """List a folder (or search) in a file-level-restore mount. Read-only."""
    if search:
        return await vbr_client.post(
            f"/api/v1/backupBrowser/flr/{session_id}/search",
            json={"path": path, "searchString": search},
            read_only=True,
            timeout=_LONG,
        )
    body: dict[str, Any] = {"path": path, "pagination": {"skip": skip, "limit": limit}}
    if item_types:
        body["filter"] = {"itemTypes": item_types}
    return await vbr_client.post(f"/api/v1/backupBrowser/flr/{session_id}/browse", json=body, read_only=True)


@mcp.tool(**tool("restore", "destructive", mixed=True))
async def restore_files(
    session_id: UUID,
    paths: list[str],
    restore_type: Literal["Keep", "Overwrite", "PermissionOnly"],
    destination: Literal["original", "another"] = "original",
    target_host: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "destination='another': FlrRestoreTargetHostModel - {'type': 'VMware'|'HyperV'|'CloudDirector'|"
                "'Agent', ...} (subtypes: VmwareFlrRestoreTargetHostModel etc., see describe_model)."
            )
        ),
    ] = None,
    target_path: str | None = None,
    credentials_id: UUID | None = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Restore files/folders from a file-level-restore mount.

    `original`: back to the same machine and path. `restore_type`: `Keep` keeps existing files and writes the
    restored ones next to them under a `RESTORED-` name, `Overwrite` replaces existing files, `PermissionOnly`
    restores only permissions.
    `another`: to `target_host` / `target_path`. `credentials_id`: guest credentials if VBR cannot use the job's.
    """
    args = tool_args(locals())
    body: dict[str, Any] = {"sourcePath": paths, "restoreType": restore_type}
    if credentials_id:
        body["credentialsId"] = credentials_id
    if destination == "another":
        if not target_host or not target_path:
            raise ToolError("destination='another' needs `target_host` and `target_path`.")
        body |= {"targetHost": target_host, "targetPath": target_path}
        path = f"/api/v1/backupBrowser/flr/{session_id}/restoreTo"
    else:
        if target_path:
            body["targetPath"] = target_path
        path = f"/api/v1/backupBrowser/flr/{session_id}/restore"
    impact = {
        "Overwrite": "Existing files at the target are OVERWRITTEN.",
        "Keep": "Existing files are kept; restored copies are written next to them.",
        "PermissionOnly": "Only permissions (ACLs) of existing items are changed.",
    }[restore_type]
    return await run(
        ctx,
        "restore_files",
        Level.DESTRUCTIVE if restore_type != "Keep" else Level.WRITE,
        f"Restore {len(paths)} item(s) to {'original location' if destination == 'original' else target_path}",
        Request("POST", path, body=body, timeout=_LONG),
        args=args,
        confirm_token=confirm_token,
        impact=[impact],
        preview={"items": paths[:50]},
    )


@mcp.tool(**tool("restore", "write"))
async def unmount_file_level_restore(
    session_id: UUID, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Unmount a file-level-restore mount when you are done with it."""
    args = tool_args(locals())
    return await run(
        ctx,
        "unmount_file_level_restore",
        Level.WRITE,
        f"Unmount file-level restore {session_id}",
        Request("POST", f"/api/v1/restore/flr/{session_id}/unmount"),
        args=args,
        confirm_token=confirm_token,
    )
