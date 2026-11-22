"""Credentials, encryption passwords, users/roles/MFA and the Security & Compliance Analyzer.

Adding / editing / removing records: `create_object` / `update_object` / `delete_object` with kinds `credentials`,
`cloud_credentials`, `encryption_password`, `kms_server`, `user`, `role`. MFA settings: `update_settings` area
`security_mfa`. Secrets passed to these tools are redacted in plans and in the audit log.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastmcp import Context
from fastmcp.exceptions import ToolError

from vbr_write_mcp.annotations import READ, tool
from vbr_write_mcp.safety import Level, Request, name_of, run, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp, vbr_client


@mcp.tool(**tool("security", "destructive"))
async def change_credentials_secret(
    credentials_id: UUID,
    what: Literal["password", "linux_root_password", "linux_private_key", "cloud_secret_key"],
    secret: str,
    passphrase: str | None = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Rotate the secret of a stored credentials record used by VBR to reach servers, guests or cloud accounts.

    `password` (standard or Linux account), `linux_root_password`, `linux_private_key` (`secret` = the key,
    optional `passphrase`), `cloud_secret_key` (secret key of a cloud credentials record). Every job and
    server using the record uses the new secret from the next connection - a wrong value breaks them.
    """
    args = tool_args(locals())
    if what == "cloud_secret_key":
        rec = await try_get(f"/api/v1/cloudCredentials/{credentials_id}")
        body: dict[str, Any] = {"newSecretKey": secret}
        req = Request("POST", f"/api/v1/cloudCredentials/{credentials_id}/changeSecretKey", body=body)
    else:
        rec = await try_get(f"/api/v1/credentials/{credentials_id}")
        segment, body = {
            "password": ("changepassword", {"password": secret}),
            "linux_root_password": ("changerootpassword", {"password": secret}),
            "linux_private_key": (
                "changeprivatekey",
                {"privateKey": secret} | ({"passphrase": passphrase} if passphrase else {}),
            ),
        }[what]
        req = Request("POST", f"/api/v1/credentials/{credentials_id}/{segment}", body=body)
    return await run(
        ctx,
        "change_credentials_secret",
        Level.DESTRUCTIVE,
        f"Change {what.replace('_', ' ')} of credentials {name_of(rec, credentials_id)}",
        req,
        args=args,
        confirm_token=confirm_token,
        preview={"credentials": summarize(rec)},
        impact=["Every job, server and restore using this record switches to the new secret."],
    )


@mcp.tool(**tool("security", "destructive"))
async def change_encryption_password(
    encryption_password_id: UUID,
    new_password: str,
    new_hint: str,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Change an encryption password (and its hint). Jobs using it encrypt new backups with the new password;
    existing backups still need the old one to restore - keep it safe."""
    args = tool_args(locals())
    rec = await try_get(f"/api/v1/encryptionPasswords/{encryption_password_id}")
    return await run(
        ctx,
        "change_encryption_password",
        Level.DESTRUCTIVE,
        f"Change encryption password {name_of(rec, encryption_password_id)}",
        Request(
            "POST",
            f"/api/v1/encryptionPasswords/{encryption_password_id}/changepassword",
            body={"password": new_password, "hint": new_hint},
        ),
        args=args,
        confirm_token=confirm_token,
        preview={"record": summarize(rec)},
        impact=["Existing backups stay encrypted with the OLD password: keep it to be able to restore them."],
    )


@mcp.tool(annotations=READ, tags={"security", "read"})
async def verify_encryption_password(encryption_password_id: UUID, password: str) -> Any:
    """Check whether `password` matches a stored encryption password (e.g. before a restore). Changes nothing."""
    return await vbr_client.post(
        f"/api/v1/encryptionPasswords/{encryption_password_id}/verify", json={"password": password}, read_only=True
    )


@mcp.tool(**tool("security", "destructive"))
async def manage_user_security(
    user_id: UUID,
    action: Literal["reset_mfa", "enable_service_account", "disable_service_account"],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Reset a user's MFA (they must enroll again at next login), or switch the service-account mode (service
    accounts are exempt from MFA - use for automation accounts only). Roles: `update_object(kind='user')`."""
    args = tool_args(locals())
    user = await try_get(f"/api/v1/security/users/{user_id}")
    if action == "reset_mfa":
        req = Request("POST", f"/api/v1/security/users/{user_id}/resetMFA")
    else:
        req = Request(
            "POST",
            f"/api/v1/security/users/{user_id}/changeServiceAccountMode",
            body={"isServiceAccountEnable": action == "enable_service_account"},
        )
    return await run(
        ctx,
        "manage_user_security",
        Level.DESTRUCTIVE,
        f"{action.replace('_', ' ')} for {name_of(user, user_id)}",
        req,
        args=args,
        confirm_token=confirm_token,
        preview={"user": summarize(user)},
    )


@mcp.tool(**tool("security", "write"))
async def clone_role(role_id: UUID, ctx: Context | None = None, confirm_token: str | None = None) -> dict[str, Any]:
    """Clone a custom role; adjust the copy with `update_object(kind='role', ...)`."""
    args = tool_args(locals())
    role = await try_get(f"/api/v1/security/roles/{role_id}")
    return await run(
        ctx,
        "clone_role",
        Level.WRITE,
        f"Clone role {name_of(role, role_id)}",
        Request("POST", f"/api/v1/security/roles/{role_id}/clone"),
        args=args,
        confirm_token=confirm_token,
    )


@mcp.tool(**tool("security", "destructive", mixed=True))
async def security_analyzer(
    action: Literal["start", "reset", "reset_all", "suppress"],
    best_practice_id: UUID | None = None,
    note: str | None = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Security & Compliance Analyzer: `start` a scan; `suppress` one best-practice finding (with a `note` - it
    stops being reported, so it is confirmed); `reset` one finding's status or `reset_all`. Schedule:
    `update_settings(area='security_analyzer_schedule')`."""
    args = tool_args(locals())
    base = "/api/v1/securityAnalyzer"
    if action in ("reset", "suppress") and best_practice_id is None:
        raise ToolError(f"action='{action}' needs best_practice_id.")
    req, level = {
        "start": (Request("POST", f"{base}/start"), Level.WRITE),
        "reset_all": (Request("POST", f"{base}/bestPractices/resetAll"), Level.WRITE),
        "reset": (Request("POST", f"{base}/bestPractices/{best_practice_id}/reset"), Level.WRITE),
        "suppress": (
            Request("POST", f"{base}/bestPractices/{best_practice_id}/suppress", body={"note": note} if note else {}),
            Level.DESTRUCTIVE,
        ),
    }[action]
    return await run(
        ctx,
        "security_analyzer",
        level,
        f"Security analyzer: {action.replace('_', ' ')}",
        req,
        args=args,
        confirm_token=confirm_token,
    )
