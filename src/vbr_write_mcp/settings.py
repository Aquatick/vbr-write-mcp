"""Configuration, read from the environment and `.env` next to the project.

The `VBR_*` connection variables have the same names and meaning as in Veeam's `vbr-mcp`, so one `.env` can serve
both servers. The `VBRW_*` variables control what this write server is allowed to do.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(os.getenv("ENV_FILE_OVERRIDE") or PROJECT_ROOT / ".env")


def _csv(value: object) -> object:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


class VbrSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VBR_")

    base_url: str = Field(default="https://localhost:9419", description="Base URL of the VBR REST API server.")
    username: str = Field(default="", description="VBR REST API username used for the password grant.")
    password: str = Field(default="", description="VBR REST API password used for the password grant.")
    verify_tls: bool = Field(default=True, description="Verify the VBR server's TLS certificate.")
    api_version: str = Field(default="1.3-rev2", description="VBR REST API version sent via the x-api-version header.")
    request_timeout_sec: float = Field(default=30.0, description="Per-request timeout for calls to the VBR server.")


AccessLevel = Literal["read", "write", "destructive"]
ConfirmationMode = Literal["auto", "elicit", "token", "none"]


class WriteSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VBRW_")

    access_level: AccessLevel = Field(
        default="destructive",
        description=(
            "Highest tool class exposed: 'read' (helpers only), 'write' (additive/reversible actions), "
            "'destructive' (everything, incl. delete/restore/failover). Use 'write' for production."
        ),
    )
    confirmation_mode: ConfirmationMode = Field(
        default="auto",
        description=(
            "How destructive calls are confirmed: 'elicit' asks the user directly through the MCP client, 'token' "
            "returns a plan plus a one-time confirm_token that the agent must send back, 'auto' uses elicit when "
            "the client supports it and token otherwise, 'none' disables server-side confirmation."
        ),
    )
    confirm_writes: bool = Field(
        default=False, description="Also require confirmation for non-destructive write tools."
    )
    confirmation_ttl_sec: int = Field(default=300, description="Lifetime of a confirm_token, in seconds.")
    dry_run: bool = Field(
        default=False, description="Never send a state-changing request; return the request that would be sent."
    )
    audit_log: str = Field(
        default=str(PROJECT_ROOT / "audit.jsonl"),
        description="JSONL audit log of every write attempt (secrets redacted). Empty string disables it.",
    )
    tool_groups: Annotated[list[str], NoDecode] = Field(
        default=[],
        description=(
            "Comma-separated tool groups to expose (empty = all): generic, objects, jobs, sessions, backups, "
            "restore, replication, agents, malware, license, security, infrastructure, system."
        ),
    )
    disabled_tools: Annotated[list[str], NoDecode] = Field(
        default=[], description="Comma-separated tool names to hide, e.g. api_write,delete_backup."
    )
    blocked_paths: Annotated[list[str], NoDecode] = Field(
        default=[],
        description="Comma-separated regexes of API paths api_write must refuse, on top of the built-in denylist.",
    )

    @field_validator("tool_groups", "disabled_tools", "blocked_paths", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        return _csv(value)


class McpAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCP_")

    enabled: bool = Field(default=True, description="Require a bearer token from MCP clients (HTTP transport only).")
    tokens: dict[str, str] = Field(default={}, description='Accepted bearer tokens, e.g. {"<token>": "my-client"}.')


class TransportSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VBRW_")

    transport: Literal["stdio", "http"] = Field(default="stdio", description="MCP transport: stdio or http.")
    http_host: str = Field(default="127.0.0.1", description="Bind address for the HTTP transport.")
    http_port: int = Field(default=8001, description="Port for the HTTP transport.")


vbr_settings = VbrSettings()
write_settings = WriteSettings()
mcp_auth_settings = McpAuthSettings()
transport_settings = TransportSettings()
