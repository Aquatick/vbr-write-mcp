"""Veeam Agent management: protection groups and the computers they discovered. Create / edit / remove a
protection group: `create_object` / `update_object` / `delete_object` with kind='protection_group'."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastmcp import Context
from fastmcp.exceptions import ToolError

from vbr_write_mcp.annotations import tool
from vbr_write_mcp.safety import Level, Request, name_of, run, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp

_PG = "/api/v1/agents/protectionGroups"

EntityAction = Literal[
    "install_agent",
    "upgrade_agent",
    "uninstall_agent",
    "install_cbt_driver",
    "uninstall_cbt_driver",
    "uninstall_all_components",
    "rescan",
    "reboot",
    "trust",
    "prepare_for_remote_bmr",
    "recreate_embedded_recovery_media",
    "remove",
]

_ENTITY_ACTIONS: dict[str, tuple[str, Level, str]] = {
    "install_agent": ("installAgent", Level.WRITE, "Installs Veeam Agent; may require a reboot of the computer."),
    "upgrade_agent": ("upgradeAgent", Level.WRITE, "Upgrades Veeam Agent; may require a reboot."),
    "install_cbt_driver": ("installCBTDriver", Level.WRITE, "Installs the CBT driver; a reboot is required."),
    "rescan": ("rescan", Level.WRITE, ""),
    "trust": ("trust", Level.WRITE, "Adds the computers' certificates/fingerprints to the trusted hosts list."),
    "prepare_for_remote_bmr": ("prepareForRemoteBmr", Level.WRITE, ""),
    "recreate_embedded_recovery_media": ("recreateEmbeddedRm", Level.WRITE, ""),
    "uninstall_agent": ("uninstallAgent", Level.DESTRUCTIVE, "Computers stop being backed up by agent jobs."),
    "uninstall_cbt_driver": ("uninstallCBTDriver", Level.DESTRUCTIVE, "Next backups read full disks; reboot needed."),
    "uninstall_all_components": ("uninstallAllComponents", Level.DESTRUCTIVE, "Removes every Veeam component."),
    "reboot": ("reboot", Level.DESTRUCTIVE, "The computers are REBOOTED now - production downtime."),
}


@mcp.tool(**tool("agents", "destructive", mixed=True))
async def set_protection_group_enabled(
    protection_group_id: UUID, enabled: bool, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Enable or disable a protection group. A disabled group stops discovery and agent deployment for its
    computers (disabling is confirmed by the server)."""
    args = tool_args(locals())
    pg = await try_get(f"{_PG}/{protection_group_id}")
    return await run(
        ctx,
        "set_protection_group_enabled",
        Level.WRITE if enabled else Level.DESTRUCTIVE,
        f"{'Enable' if enabled else 'Disable'} protection group {name_of(pg, protection_group_id)}",
        Request("POST", f"{_PG}/{protection_group_id}/{'enable' if enabled else 'disable'}"),
        args=args,
        confirm_token=confirm_token,
        preview={"protectionGroup": summarize(pg)},
    )


@mcp.tool(**tool("agents", "write"))
async def rescan_protection_group(
    protection_group_id: UUID, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Rescan a protection group now: discover new computers and refresh agent state. Returns a session."""
    args = tool_args(locals())
    pg = await try_get(f"{_PG}/{protection_group_id}")
    return await run(
        ctx,
        "rescan_protection_group",
        Level.WRITE,
        f"Rescan protection group {name_of(pg, protection_group_id)}",
        Request("POST", f"{_PG}/{protection_group_id}/rescan"),
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("agents", "destructive", mixed=True))
async def manage_discovered_computers(
    protection_group_id: UUID,
    action: EntityAction,
    entity_ids: list[UUID],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Act on computers a protection group discovered (ids from vbr-mcp `get_protection_group_entities`).

    Additive actions run directly: install/upgrade agent, install CBT driver, rescan, trust, prepare for remote
    bare-metal recovery, recreate embedded recovery media. Confirmed by the server: uninstall agent / CBT driver /
    all components, reboot, remove (removes one computer from the group - pass exactly one id).
    """
    args = tool_args(locals())
    if not entity_ids:
        raise ToolError("Pass at least one entity id.")
    if action == "remove":
        if len(entity_ids) != 1:
            raise ToolError("action='remove' takes exactly one entity id per call.")
        request = Request("DELETE", f"{_PG}/{protection_group_id}/discoveredEntities/{entity_ids[0]}")
        level, impact = Level.DESTRUCTIVE, "The computer is removed from the protection group."
    else:
        segment, level, impact = _ENTITY_ACTIONS[action]
        request = Request(
            "POST", f"{_PG}/{protection_group_id}/discoveredEntities/{segment}", body={"entityIds": entity_ids}
        )
    pg = await try_get(f"{_PG}/{protection_group_id}")
    return await run(
        ctx,
        "manage_discovered_computers",
        level,
        f"{action.replace('_', ' ').capitalize()} on {len(entity_ids)} computer(s) of {name_of(pg, protection_group_id)}",
        request,
        args=args,
        confirm_token=confirm_token,
        impact=[impact],
        preview={"protectionGroup": summarize(pg), "entityIds": [str(e) for e in entity_ids]},
    )


@mcp.tool(**tool("agents", "destructive", mixed=True))
async def set_agent_backup_attachment(
    discovered_entity_id: UUID,
    backup_ids: list[UUID],
    attach: bool,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Attach existing backups to a managed agent computer (e.g. after reinstalling it), or detach them."""
    args = tool_args(locals())
    action = "attachBackup" if attach else "detachBackup"
    return await run(
        ctx,
        "set_agent_backup_attachment",
        Level.WRITE if attach else Level.DESTRUCTIVE,
        f"{'Attach' if attach else 'Detach'} {len(backup_ids)} backup(s) {'to' if attach else 'from'} agent "
        f"{discovered_entity_id}",
        Request(
            "POST", f"/api/v1/agents/discoveredEntities/{discovered_entity_id}/{action}", body={"backupIds": backup_ids}
        ),
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("agents", "write"))
async def reboot_recovery_appliances(
    appliance_ids: list[UUID] | None = None, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Reboot Veeam Agent recovery appliances (one by id, or all when `appliance_ids` is omitted)."""
    args = tool_args(locals())
    if appliance_ids and len(appliance_ids) == 1:
        req = Request("POST", f"/api/v1/agents/recoveryAppliances/{appliance_ids[0]}/reboot")
    else:
        req = Request(
            "POST", "/api/v1/agents/recoveryAppliances/reboot", body={"ids": appliance_ids} if appliance_ids else None
        )
    return await run(
        ctx,
        "reboot_recovery_appliances",
        Level.WRITE,
        "Reboot recovery appliance(s)",
        req,
        args=args,
        confirm_token=confirm_token,
    )
