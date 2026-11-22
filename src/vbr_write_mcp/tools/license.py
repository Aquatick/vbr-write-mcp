"""License administration beyond vbr-mcp's `revoke_license`."""

from __future__ import annotations

import base64
from typing import Annotated, Any, Literal
from uuid import UUID

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from vbr_write_mcp.annotations import tool
from vbr_write_mcp.safety import Level, Request, run, tool_args
from vbr_write_mcp.server import mcp

_LIC = "/api/v1/license"


@mcp.tool(**tool("license", "destructive"))
async def install_license(
    license_base64: Annotated[str, Field(description="The .lic file content, Base64-encoded.")],
    force_standalone_mode: bool = False,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Install a license file on the backup server, replacing the installed license. Confirmed by the server."""
    args = tool_args(locals())
    try:
        base64.b64decode(license_base64, validate=True)
    except ValueError as e:
        raise ToolError("license_base64 is not valid Base64: encode the .lic file content first.") from e
    return await run(
        ctx,
        "install_license",
        Level.DESTRUCTIVE,
        "Install a new license file",
        Request(
            "POST", f"{_LIC}/install", body={"license": license_base64, "forceStandaloneMode": force_standalone_mode}
        ),
        args=args,
        confirm_token=confirm_token,
        impact=["The currently installed license is replaced."],
    )


@mcp.tool(**tool("license", "write"))
async def refresh_license(
    action: Literal["renew", "update"],
    force_standalone_mode: bool = False,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """`renew`: download the renewed license from Veeam for the installed one; `update`: fetch the current license
    state (e.g. after a contract change) from the Veeam licensing server. Needs internet access on the server."""
    args = tool_args(locals())
    body = {"forceStandaloneMode": force_standalone_mode} if action == "renew" else None
    return await run(
        ctx,
        "refresh_license",
        Level.WRITE,
        f"License {action}",
        Request("POST", f"{_LIC}/{action}", body=body),
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("license", "write"))
async def set_license_options(
    auto_update: bool | None = None,
    proactive_support: bool | None = None,
    unlicensed_agent_consumption: Annotated[
        bool | None, Field(description="Let unlicensed Veeam Agents consume instances from the license.")
    ] = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Switch license options: automatic license update (with proactive support) and instance consumption by
    unlicensed agents. Only the options you pass are changed."""
    args = tool_args(locals())
    requests: list[Request] = []
    if auto_update is not None:
        body: dict[str, Any] = {"enabled": auto_update}
        if proactive_support is not None:
            body["proactiveSupportEnabled"] = proactive_support
        requests.append(Request("POST", f"{_LIC}/autoupdate", body=body, label="auto update"))
    elif proactive_support is not None:
        raise ToolError("proactive_support is set together with auto_update.")
    if unlicensed_agent_consumption is not None:
        requests.append(
            Request(
                "POST",
                f"{_LIC}/agentConsumption",
                body={"enabled": unlicensed_agent_consumption},
                label="agent consumption",
            )
        )
    if not requests:
        raise ToolError("Pass at least one option.")
    return await run(
        ctx,
        "set_license_options",
        Level.WRITE,
        "Change license options",
        requests,
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("license", "destructive", mixed=True))
async def manage_instance_license(
    instance_id: UUID,
    action: Literal["assign_server", "assign_workstation", "remove"],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Assign an instance license to a workload as Server or Workstation, or `remove` the workload's instance
    license (it then needs a license again at the next backup)."""
    args = tool_args(locals())
    if action == "remove":
        req = Request("POST", f"{_LIC}/instances/{instance_id}/remove")
        level = Level.DESTRUCTIVE
    else:
        role = "Server" if action == "assign_server" else "Workstation"
        req = Request("POST", f"{_LIC}/instances/{instance_id}/assign", body={"role": role})
        level = Level.WRITE
    return await run(
        ctx,
        "manage_instance_license",
        level,
        f"{action.replace('_', ' ')} instance license {instance_id}",
        req,
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("license", "destructive"))
async def remove_installed_license(
    section: Literal["All", "Instance", "Socket"] = "All",
    force_standalone_mode: bool = False,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Remove the installed license (or only its Instance or Socket section). Jobs stop processing unlicensed
    workloads. Confirmed by the server."""
    args = tool_args(locals())
    return await run(
        ctx,
        "remove_installed_license",
        Level.DESTRUCTIVE,
        f"Remove installed license ({section})",
        Request("POST", f"{_LIC}/remove", body={"section": section, "forceStandaloneMode": force_standalone_mode}),
        args=args,
        confirm_token=confirm_token,
        impact=["Workloads covered by the removed license are no longer processed by jobs."],
    )
