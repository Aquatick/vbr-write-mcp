"""Create / edit / remove for every VBR object collection, and edits of the server-wide settings singletons.

Four tools cover ~60 endpoints: the kind decides the collection path, and the bundled API catalog supplies the body
schema (`describe_endpoint`). Edits are patch-style: only the changed fields are passed; the tool reads the current
object, deep-merges the changes and sends the whole model, which is what VBR's PUT expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from fastmcp import Context
from fastmcp.exceptions import ToolError

from vbr_write_mcp.annotations import tool
from vbr_write_mcp.merge import deep_merge, diff
from vbr_write_mcp.safety import Level, Plan, Request, execute, object_hash, summarize, tool_args, try_get
from vbr_write_mcp.server import mcp, vbr_client


@dataclass(frozen=True)
class Kind:
    path: str
    create_model: str | None
    update: bool = True
    delete: bool = True
    update_path: str = "{base}/{id}"
    delete_params: tuple[str, ...] = ()
    note: str = ""


KINDS: dict[str, Kind] = {
    "job": Kind("/api/v1/jobs", "JobSpec", note="Deleting a job keeps its backups (they become orphaned)."),
    "managed_server": Kind(
        "/api/v1/backupInfrastructure/managedServers",
        "ManagedServerSpec",
        note="Adding a server returns a session (the add runs asynchronously). Removing fails while jobs use it.",
    ),
    "repository": Kind(
        "/api/v1/backupInfrastructure/repositories",
        "RepositorySpec",
        delete_params=("deleteBackups",),
        note="Removing a repository with deleteBackups=true also deletes every backup stored in it.",
    ),
    "scale_out_repository": Kind(
        "/api/v1/backupInfrastructure/scaleOutRepositories", "ScaleOutRepositorySpec", delete_params=("deleteBackups",)
    ),
    "proxy": Kind("/api/v1/backupInfrastructure/proxies", "ProxySpec"),
    "mount_server": Kind("/api/v1/backupInfrastructure/mountServers", "MountServerSpec", delete=False),
    "credentials": Kind("/api/v1/credentials", "CredentialsSpec"),
    "cloud_credentials": Kind("/api/v1/cloudCredentials", "CloudCredentialsSpec"),
    "encryption_password": Kind(
        "/api/v1/encryptionPasswords",
        "EncryptionPasswordSpec",
        note="Editing an encryption password changes only its hint. Removing one makes backups encrypted with it "
        "unrecoverable unless the password is known elsewhere.",
    ),
    "kms_server": Kind("/api/v1/kmsServers", "KeyManagementServerBaseSpec"),
    "protection_group": Kind(
        "/api/v1/agents/protectionGroups",
        "ProtectionGroupSpec",
        delete_params=("uninstallEverything",),
        note="Removing with uninstallEverything=true uninstalls Veeam Agents from every computer in the group.",
    ),
    "recovery_token": Kind("/api/v1/agents/recoveryTokens", "ComputerRecoveryTokenSpec"),
    "unstructured_data_server": Kind("/api/v1/inventory/unstructuredDataServers", "UnstructuredDataServerSpec"),
    "entra_id_tenant": Kind("/api/v1/inventory/entraId/tenants", "EntraIDTenantSpec"),
    "ad_domain": Kind("/api/v1/adDomains", "ADDomainSpec", update=False),
    "global_vm_exclusion": Kind(
        "/api/v1/globalExclusions/vm",
        "GlobalVMExclusionSpec",
        update_path="{base}/{id}/note",
        note="Editing a global VM exclusion changes only its note: pass changes={'note': '...'}.",
    ),
    "role": Kind("/api/v1/security/roles", "CustomRoleSpec"),
    "user": Kind(
        "/api/v1/security/users",
        "UserSpec",
        update_path="{base}/{id}/roles",
        note="Editing a user or group changes its roles: pass changes={'roles': [...]} with the complete role list.",
    ),
    "datastore_latency": Kind("/api/v1/generalOptions/storageLatency/datastores", "DatastoresLatencySettingsSpec"),
}

ObjectKind = Literal[
    "job",
    "managed_server",
    "repository",
    "scale_out_repository",
    "proxy",
    "mount_server",
    "credentials",
    "cloud_credentials",
    "encryption_password",
    "kms_server",
    "protection_group",
    "recovery_token",
    "unstructured_data_server",
    "entra_id_tenant",
    "ad_domain",
    "global_vm_exclusion",
    "role",
    "user",
    "datastore_latency",
]

SETTINGS: dict[str, str] = {
    "config_backup": "/api/v1/configBackup",
    "general_options": "/api/v1/generalOptions",
    "email": "/api/v1/generalOptions/emailSettings",
    "notifications": "/api/v1/generalOptions/notifications",
    "event_forwarding": "/api/v1/generalOptions/eventForwarding",
    "host_authentication": "/api/v1/generalOptions/hostAuthentication",
    "storage_latency": "/api/v1/generalOptions/storageLatency",
    "node_exporter": "/api/v1/generalOptions/nodeExporterSettings",
    "vsa_event_forwarding": "/api/v1/generalOptions/vsaEventForwarding",
    "vsa_event_forwarding_daemon": "/api/v1/generalOptions/vsaEventForwarding/daemonSettings",
    "traffic_rules": "/api/v1/trafficRules",
    "malware_general": "/api/v1/malwareDetection/settings/general",
    "malware_suspicious_files": "/api/v1/malwareDetection/settings/fileDetection/suspiciousFileMasks",
    "security_mfa": "/api/v1/security/settings",
    "security_analyzer_schedule": "/api/v1/securityAnalyzer/schedule",
    # per-object sub-settings: need `id`
    "repository_access_permissions": "/api/v1/backupInfrastructure/repositories/{id}/accessPermissions",
    "hyperv_server_volumes": "/api/v1/backupInfrastructure/managedServers/{id}/volumes",
    "azure_instant_recovery_switchover": "/api/v1/restore/instantRecovery/azure/vm/{id}/switchoverSettings",
    "file_share_instant_recovery_switchover": "/api/v1/restore/instantRecovery/unstructuredData/{id}/switchoverSettings",
}

SettingsArea = Literal[
    "config_backup",
    "general_options",
    "email",
    "notifications",
    "event_forwarding",
    "host_authentication",
    "storage_latency",
    "node_exporter",
    "vsa_event_forwarding",
    "vsa_event_forwarding_daemon",
    "traffic_rules",
    "malware_general",
    "malware_suspicious_files",
    "security_mfa",
    "security_analyzer_schedule",
    "repository_access_permissions",
    "hyperv_server_volumes",
    "azure_instant_recovery_switchover",
    "file_share_instant_recovery_switchover",
]

_KIND_LIST = ", ".join(f"`{k}`" for k in KINDS)


@mcp.tool(
    **tool("objects", "write"),
    description=(
        "Create a VBR object from a full `spec` (the POST body of its collection).\n\n"
        f"Kinds: {_KIND_LIST}. Look the body up first with `describe_endpoint('<collection path>', 'POST')`; most "
        "specs are polymorphic on `type` (see `x-subtypes`, e.g. `VSphereBackupJobSpec`/`BackupJobSpec` for "
        "`job`, `WindowsLocalStorageSpec`/`LinuxHardenedStorageSpec`/`SmbStorageSpec` for `repository`, `ViHostSpec`"
        "/`CloudDirectorHostSpec` for `managed_server`, `StandardCredentialsSpec`/`LinuxCredentialsSpec` for "
        "`credentials`). Additive, so no confirmation is needed by default; adding servers and repositories runs "
        "as a session."
    ),
)
async def create_object(
    kind: ObjectKind,
    spec: dict[str, Any],
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    args = tool_args(locals())
    k = KINDS[kind]
    impact = [k.note] if k.note else []
    name = spec.get("name") or spec.get("username") or spec.get("hint") or ""
    plan = Plan(
        tool="create_object",
        level=Level.WRITE,
        summary=f"Create {kind}" + (f" {name!r}" if name else ""),
        requests=[Request("POST", k.path, body=spec, timeout=120)],
        impact=impact,
    )
    return await execute(ctx, plan, args=args, confirm_token=confirm_token)


@mcp.tool(
    **tool("objects", "destructive"),
    description=(
        "Edit an existing VBR object: pass only the fields to change in `changes`; the tool reads the object, "
        "deep-merges `changes` into it and sends the complete model (VBR PUT replaces the whole object). Nested "
        "objects merge field by field; lists are replaced as a whole, so send the full new list. `replace=True` "
        "sends `changes` as the complete body instead.\n\n"
        f"Kinds: {_KIND_LIST} (not `ad_domain`). `user` edits the role list, `global_vm_exclusion` the note, "
        "`encryption_password` the hint. The confirmation plan shows a field-level diff. Example: disable job "
        "encryption -> kind='job', changes={'storage': {'advancedSettings': {'storageData': {'encryption': "
        "{'isEnabled': false}}}}}. Edits can change what is protected and how: confirmed by the server."
    ),
)
async def update_object(
    kind: ObjectKind,
    id: UUID,
    changes: dict[str, Any],
    replace: bool = False,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    args = tool_args(locals())
    k = KINDS[kind]
    if not k.update:
        raise ToolError(f"VBR has no edit operation for {kind}: remove and re-create it instead.")
    base_path = f"{k.path}/{id}"
    put_path = k.update_path.format(base=k.path, id=id)
    current = await vbr_client.get(put_path if kind == "user" else base_path) if kind != "global_vm_exclusion" else {}
    if kind == "global_vm_exclusion":
        new_body: Any = changes
        changes_view = diff({}, changes)
    else:
        new_body = changes if replace else deep_merge(current, changes)
        changes_view = diff(current, new_body)
    if not changes_view:
        return {"status": "no_change", "message": f"The {kind} already has these values; nothing was sent."}
    obj = current if kind != "user" else await try_get(base_path)
    plan = Plan(
        tool="update_object",
        level=Level.DESTRUCTIVE,
        summary=f"Edit {kind} {obj.get('name', id) if isinstance(obj, dict) else id}",
        requests=[Request("PUT", put_path, body=new_body, timeout=120)],
        impact=[k.note] if k.note else [],
        preview={"target": summarize(obj), "changes": changes_view},
    )
    return await execute(ctx, plan, args=args, confirm_token=confirm_token, binding=object_hash(current))


@mcp.tool(
    **tool("objects", "destructive"),
    description=(
        "Remove a VBR object.\n\n"
        f"Kinds: {_KIND_LIST} (not `mount_server`). Optional `options` are the DELETE query parameters: "
        "`deleteBackups` (repository, scale_out_repository - also deletes the backups stored there), "
        "`uninstallEverything` (protection_group - uninstalls agents). Use `delete_backup` for backups. "
        "Confirmed by the server."
    ),
)
async def delete_object(
    kind: ObjectKind,
    id: UUID,
    options: dict[str, bool] | None = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    args = tool_args(locals())
    k = KINDS[kind]
    if not k.delete:
        raise ToolError(f"VBR has no remove operation for {kind}.")
    unknown = set(options or {}) - set(k.delete_params)
    if unknown:
        raise ToolError(f"Unsupported option(s) {sorted(unknown)} for {kind}; allowed: {list(k.delete_params)}.")
    path = f"{k.path}/{id}"
    target = summarize(await try_get(path)) if kind not in ("global_vm_exclusion",) else {"id": str(id)}
    impact = [k.note] if k.note else []
    if (options or {}).get("deleteBackups"):
        impact.insert(0, "deleteBackups=true: every backup in this repository is deleted from disk.")
    if (options or {}).get("uninstallEverything"):
        impact.insert(0, "uninstallEverything=true: Veeam components are uninstalled from all group members.")
    name = target.get("name", id) if isinstance(target, dict) else id
    plan = Plan(
        tool="delete_object",
        level=Level.DESTRUCTIVE,
        summary=f"Remove {kind} {name}",
        requests=[Request("DELETE", path, params=options or None)],
        impact=impact,
        preview={"target": target},
    )
    return await execute(ctx, plan, args=args, confirm_token=confirm_token)


_AREA_LIST = ", ".join(f"`{a}`" for a in SETTINGS)


@mcp.tool(
    **tool("objects", "destructive"),
    description=(
        "Edit server-wide VBR settings (or a per-object settings block) with patch semantics: pass only the "
        "fields to change; the tool reads the current settings, merges and sends the full model, and the "
        "confirmation plan shows the diff. Lists are replaced as a whole.\n\n"
        f"Areas: {_AREA_LIST}. Areas whose path has an `{{id}}` need `id` (repository, Hyper-V server or Instant "
        "Recovery mount). Look the model up with `describe_endpoint('<path>', 'PUT')` when unsure of field names. "
        "Example: config backup retention -> area='config_backup', changes={'restorePointsToKeep': 30}."
    ),
)
async def update_settings(
    area: SettingsArea,
    changes: dict[str, Any],
    id: UUID | None = None,
    ctx: Context | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    args = tool_args(locals())
    template = SETTINGS[area]
    if "{id}" in template:
        if id is None:
            raise ToolError(f"Area {area} needs `id`.")
        path = template.format(id=id)
    else:
        path = template
    current = await vbr_client.get(path)
    new_body = deep_merge(current, changes)
    changes_view = diff(current, new_body)
    if not changes_view:
        return {"status": "no_change", "message": "These settings already have these values; nothing was sent."}
    plan = Plan(
        tool="update_settings",
        level=Level.DESTRUCTIVE,
        summary=f"Edit {area.replace('_', ' ')} settings",
        requests=[Request("PUT", path, body=new_body)],
        preview={"changes": changes_view},
    )
    return await execute(ctx, plan, args=args, confirm_token=confirm_token, binding=object_hash(current))
