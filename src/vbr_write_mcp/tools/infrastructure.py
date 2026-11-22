"""Backup infrastructure actions. Add / edit / remove servers, repositories, SOBRs, proxies and mount servers:
`create_object` / `update_object` / `delete_object`. vbr-mcp covers proxy enable/disable, repository and single
server rescans, SOBR extent modes and component updates."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastmcp import Context
from pydantic import Field

from vbr_write_mcp.annotations import READ, tool
from vbr_write_mcp.safety import Level, Request, name_of, run, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp, vbr_client

_MS = "/api/v1/backupInfrastructure/managedServers"


@mcp.tool(**tool("infrastructure", "write"))
async def rescan_managed_servers(
    managed_server_ids: list[UUID] | None = None, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Rescan several managed servers at once, or all of them when `managed_server_ids` is omitted. Returns a
    session."""
    args = tool_args(locals())
    return await run(
        ctx,
        "rescan_managed_servers",
        Level.WRITE,
        f"Rescan {len(managed_server_ids) if managed_server_ids else 'all'} managed server(s)",
        Request("POST", f"{_MS}/rescan", body={"ids": managed_server_ids} if managed_server_ids else {}),
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("infrastructure", "write"))
async def rescan_inventory(
    hostname: Annotated[str, Field(description="Managed server name as VBR shows it, e.g. 'vcenter01.corp.local'.")],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Refresh the cached virtualization inventory (VMs, vApps, datastores, networks) of one managed server, e.g.
    after a VM or vApp was created and does not show up in `browse_inventory` yet."""
    args = tool_args(locals())
    return await run(
        ctx,
        "rescan_inventory",
        Level.WRITE,
        f"Rescan inventory of {hostname}",
        Request("POST", f"/api/v1/inventory/{hostname}/rescan"),
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("infrastructure", "write"))
async def set_default_mount_server(
    mount_server_id: UUID, ctx: Context | None = None, confirm_token: str | None = None
) -> dict[str, Any]:
    """Make a mount server the default one for its repositories' restores (FLR, Instant Recovery)."""
    args = tool_args(locals())
    ms = await try_get(f"/api/v1/backupInfrastructure/mountServers/{mount_server_id}")
    return await run(
        ctx,
        "set_default_mount_server",
        Level.WRITE,
        f"Set default mount server {name_of(ms, mount_server_id)}",
        Request("POST", f"/api/v1/backupInfrastructure/mountServers/{mount_server_id}/default"),
        args=args,
        confirm_token=confirm_token,
        preview={"mountServer": summarize(ms)},
    )


@mcp.tool(annotations=READ, tags={"infrastructure", "read"})
async def get_server_certificate(
    server_name: str,
    type: Literal[
        "WindowsHost",
        "LinuxHost",
        "ViHost",
        "HvServer",
        "HvCluster",
        "CloudDirectorHost",
        "NutanixCluster",
        "PrismCentral",
        "ProxmoxCluster",
        "ProxmoxNode",
        "SCVMM",
        "SmbV3Cluster",
        "SmbV3StandaloneHost",
    ],
    credentials_id: UUID | None = None,
    port: int | None = None,
) -> Any:
    """Fetch a server's TLS certificate thumbprint or SSH fingerprint - needed in the spec when adding a managed
    server with `create_object(kind='managed_server')`. Read-only (changes nothing in VBR)."""
    body: dict[str, Any] = {"serverName": server_name, "type": type}
    if credentials_id:
        body["credentialsId"] = credentials_id
    if port:
        body["port"] = port
    return await vbr_client.post("/api/v1/connectionCertificate", json=body, read_only=True, timeout=90)


@mcp.tool(annotations=READ, tags={"infrastructure", "read"})
async def discover_cloud_director_vcenters(spec: dict[str, Any]) -> Any:
    """List the vCenter Servers attached to a VMware Cloud Director server, before adding it
    (`CloudDirectorHostDiscoverySpec`: see describe_model). Read-only."""
    return await vbr_client.post(f"{_MS}/cloudDirectorHosts", json=spec, read_only=True, timeout=90)
