"""Server-level operations: configuration export/import (automation), support log export, test email, High
Availability cluster switchover/failover. Settings edits: `update_settings`."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from vbr_write_mcp.annotations import tool
from vbr_write_mcp.safety import Level, Request, run, tool_args
from vbr_write_mcp.server import mcp

_AUTOMATION = {
    "jobs": ("jobs",),
    "managed_servers": ("managedServers",),
    "repositories": ("repositories",),
    "proxies": ("proxies",),
    "credentials": ("credentials",),
    "cloud_credentials": ("cloudcredentials",),
    "encryption_passwords": ("encryptionPasswords",),
}
_SECRET_KINDS = {"credentials", "cloud_credentials", "encryption_passwords"}

ConfigKind = Literal[
    "jobs", "managed_servers", "repositories", "proxies", "credentials", "cloud_credentials", "encryption_passwords"
]


@mcp.tool(**tool("system", "destructive", mixed=True))
async def export_configuration(
    kind: ConfigKind,
    filters: Annotated[
        dict[str, Any] | None,
        Field(
            description="Optional export spec: {'ids': [...], 'names': [...], 'types': [...]} (see describe_endpoint)."
        ),
    ] = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Export VBR configuration objects as JSON import specs - for documentation, review, migration to another
    VBR server, or as a template for `create_object`. Exporting credentials / cloud credentials / encryption
    passwords is confirmed by the server because the output is sensitive."""
    args = tool_args(locals())
    segment = _AUTOMATION[kind][0]
    return await run(
        ctx,
        "export_configuration",
        Level.DESTRUCTIVE if kind in _SECRET_KINDS else Level.WRITE,
        f"Export {kind.replace('_', ' ')}",
        Request("POST", f"/api/v1/automation/{segment}/export", body=filters or {}, timeout=120),
        args=args,
        confirm_token=confirm_token,
        impact=["The export contains sensitive data (secrets in exported form)." if kind in _SECRET_KINDS else ""],
    )


@mcp.tool(**tool("system", "destructive"))
async def import_configuration(
    kind: ConfigKind,
    collection: Annotated[
        dict[str, Any],
        Field(
            description=(
                "The import collection - exactly the structure `export_configuration` returns, e.g. {'jobs': [...]}, "
                "{'proxies': [...]}, {'viHosts': [...], 'windowsHosts': [...]} for managed servers, "
                "{'LinuxHardenedRepositories': [...], ...} for repositories. See describe_endpoint."
            )
        ),
    ],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Import configuration objects (as produced by `export_configuration`, e.g. on another VBR server). Objects
    that exist already may be updated. Returns an automation session; stop it with `stop_automation_session`.
    Confirmed by the server."""
    args = tool_args(locals())
    segment = _AUTOMATION[kind][0]
    items = [i for v in collection.values() if isinstance(v, list) for i in v]
    if not items:
        raise ToolError("`collection` holds no items to import.")
    names = [str(i.get("name") or i.get("username") or i.get("hint") or "?") for i in items[:30] if isinstance(i, dict)]
    return await run(
        ctx,
        "import_configuration",
        Level.DESTRUCTIVE,
        f"Import {len(items)} {kind.replace('_', ' ')}",
        Request("POST", f"/api/v1/automation/{segment}/import", body=collection, timeout=180),
        args=args,
        confirm_token=confirm_token,
        preview={"items": names},
        impact=["Existing objects with the same identity can be overwritten."],
    )


@mcp.tool(**tool("system", "write"))
async def export_support_logs(
    scope: Literal["Jobs", "Hosts"],
    ids: list[str],
    date_from: str | None = None,
    date_to: str | None = None,
    include_postgres_logs: bool = False,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Collect a support log bundle (for a Veeam support case) for some jobs or hosts. With `date_from`/`date_to`
    (ISO-8601) only that range is exported, otherwise everything. Returns a log-export session."""
    args = tool_args(locals())
    body: dict[str, Any] = {
        "exportType": "DateRange" if (date_from or date_to) else "All",
        "scopeType": scope,
        ("jobIds" if scope == "Jobs" else "hostIds"): ids,
        "exportLocalPgLogs": include_postgres_logs,
    }
    if date_from:
        body["dateFrom"] = date_from
    if date_to:
        body["dateTo"] = date_to
    return await run(
        ctx,
        "export_support_logs",
        Level.WRITE,
        f"Export support logs for {len(ids)} {scope.lower()}",
        Request("POST", "/api/v1/exportlogs", body=body),
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("system", "write"))
async def send_test_email(ctx: Context | None = None, confirm_token: str | None = None) -> dict[str, Any]:
    """Send a test message with the configured email notification settings (`update_settings(area='email')`)."""
    args = tool_args(locals())
    return await run(
        ctx,
        "send_test_email",
        Level.WRITE,
        "Send a test notification email",
        Request("POST", "/api/v1/generalOptions/emailSettings/testMessage"),
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("system", "destructive"))
async def high_availability_cluster(
    action: Literal["switchover", "failover"],
    ignore_lag: bool = False,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Backup server High Availability cluster: planned `switchover` to the secondary node (`ignore_lag` skips
    the database replication lag check), or `failover` when the primary node is lost. The REST API and this MCP
    server reconnect to the new primary only if VBR_BASE_URL points at the cluster's shared name."""
    args = tool_args(locals())
    req = Request(
        "POST",
        f"/api/v1/highAvailabilityCluster/{action}",
        body={"ignoreLag": ignore_lag} if action == "switchover" else None,
        timeout=120,
    )
    return await run(
        ctx,
        "high_availability_cluster",
        Level.DESTRUCTIVE,
        f"High Availability cluster {action}",
        req,
        args=args,
        confirm_token=confirm_token,
        impact=["The backup server role moves to the other node: running jobs and sessions are interrupted."],
    )
