"""vbr-write-mcp: write and administration tools for Veeam Backup & Replication, complementing Veeam's vbr-mcp."""

from .settings import vbr_settings  # noqa: F401


def build():
    """Import every tool module, apply visibility settings and return the server."""
    from vbr_write_mcp.server import apply_visibility, mcp
    from vbr_write_mcp.tools import (  # noqa: F401
        agents,
        backups,
        generic,
        infrastructure,
        jobs,
        license,
        malware,
        objects,
        replication,
        restore,
        security,
        sessions,
        system,
    )

    apply_visibility()
    return mcp


def main() -> None:
    from vbr_write_mcp.settings import transport_settings

    mcp = build()
    if transport_settings.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="http", host=transport_settings.http_host, port=transport_settings.http_port)
