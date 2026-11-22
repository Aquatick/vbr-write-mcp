from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastmcp import FastMCP

from vbr_write_mcp.auth import StaticBearerVerifier
from vbr_write_mcp.settings import mcp_auth_settings, transport_settings, vbr_settings, write_settings
from vbr_write_mcp.vbr_client import VbrClient

vbr_client = VbrClient(
    base_url=vbr_settings.base_url,
    username=vbr_settings.username,
    password=vbr_settings.password,
    verify_tls=vbr_settings.verify_tls,
    api_version=vbr_settings.api_version,
    timeout=vbr_settings.request_timeout_sec,
)

INSTRUCTIONS = """\
Write and administration tools for one Veeam Backup & Replication (VBR) server over its REST API (1.3-rev2). They
complement Veeam's read-oriented `vbr-mcp`: use vbr-mcp (or `api_read` here) to find ids and check state, and these
tools to change things.

Every state-changing tool goes through a server-side safety layer:
- Destructive tools (delete, restore, failover/failback, stopping sessions, edits of existing objects, settings,
  removing licenses, marking malware clean...) are confirmed by the server. Either the user is asked directly by the
  MCP client, or the tool returns `status: confirmation_required` with a plan and a `confirm_token`. In that case
  show the plan to the user in plain words (target name, what will happen, the impact lines), ask for an explicit
  yes, and only then call the same tool again with the same arguments plus `confirm_token`. Never invent or reuse a
  token, never treat the user's original request as the yes, and never confirm on their behalf.
- `status: dry_run` means the server is in dry-run mode and nothing was sent.
- `status: submitted` means VBR accepted the request. Most actions are asynchronous: say they were started, then
  follow the returned session with `get_session_result`.

Creating or configuring something for the user (a backup job, repository, credentials, ...): never ask the user
for JSON or field names. Ask in plain language for what is missing, a few questions at a time, and offer concrete
choices read from the server instead of open questions (the VMs/vApps to protect from the inventory, the
repositories with their free space, the proxies, existing credentials and encryption passwords). For a backup job
that is: name, what to protect, target repository, retention, schedule, application-aware processing and guest
credentials, encryption, and whether a backup copy or GFS is wanted; propose sensible defaults the user can accept
with one word. Then build the body from `describe_endpoint` / `describe_model`, show a short plain-language summary
of what will be created (not JSON), and call the tool only after the user agrees.

Before any action, resolve ids with read tools; never guess or construct an id. Edits (`update_object`,
`update_settings`) take only the fields to change and merge them into the current object; lists are replaced as a
whole, so send the complete new list. When you need a request body you do not know, call `describe_endpoint` (and
`describe_model` for nested or polymorphic parts, see `x-subtypes`) instead of guessing field names. `api_write` is
the escape hatch for any write endpoint without a dedicated tool. When VBR refuses a call, report its reason and do
not repeat the same call unchanged. A question about an action ("what happens if...") is not a request to run it.
"""

try:
    _VERSION = version("vbr-write-mcp")
except PackageNotFoundError:  # running from a source tree without install
    _VERSION = "0.0.0"

mcp = FastMCP(
    "VBR-Write",
    version=_VERSION,
    instructions=INSTRUCTIONS,
    auth=(
        StaticBearerVerifier(tokens=mcp_auth_settings.tokens)
        if mcp_auth_settings.enabled and transport_settings.transport == "http"
        else None
    ),
)

GROUPS = (
    "generic",
    "objects",
    "jobs",
    "sessions",
    "backups",
    "restore",
    "replication",
    "agents",
    "malware",
    "license",
    "security",
    "infrastructure",
    "system",
)


def apply_visibility() -> None:
    """Hide tools by access level, group and name, as configured. Run after all tool modules are imported."""
    hidden_levels = {"read": {"write", "destructive"}, "write": {"destructive"}, "destructive": set()}[
        write_settings.access_level
    ]
    if hidden_levels:
        mcp.disable(tags=hidden_levels)
    unknown = set(write_settings.tool_groups) - set(GROUPS)
    if unknown:
        raise SystemExit(f"Unknown VBRW_TOOL_GROUPS entries: {sorted(unknown)}. Valid: {', '.join(GROUPS)}")
    if write_settings.tool_groups:
        mcp.disable(tags=set(GROUPS) - set(write_settings.tool_groups))  # "core" helpers always stay
    if write_settings.disabled_tools:
        mcp.disable(names=set(write_settings.disabled_tools))
