"""MCP tool annotations plus the visibility tags derived from them.

`openWorldHint` is false throughout: every tool talks only to the VBR server in `VBR_BASE_URL`.
"""

from typing import Any

READ: dict[str, Any] = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
WRITE: dict[str, Any] = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
DESTRUCTIVE: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}


def tool(group: str, level: str, *, mixed: bool = False) -> dict[str, Any]:
    """Keyword arguments for `@mcp.tool`: annotations for clients, tags for VBRW_ACCESS_LEVEL / VBRW_TOOL_GROUPS.

    `level` is the most dangerous thing the tool can do (it sets the MCP hints). A `mixed` tool also has harmless
    variants (enable vs. disable, install vs. uninstall): it stays visible at VBRW_ACCESS_LEVEL=write and the
    executor refuses only its destructive variants there.
    """
    annotations = {"read": READ, "write": WRITE, "destructive": DESTRUCTIVE}[level]
    visibility = "write" if mixed else level
    return {"annotations": annotations, "tags": {group, visibility}}
