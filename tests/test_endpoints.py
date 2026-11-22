"""Every write tool, driven through the MCP protocol against a fake VBR.

Checks that each request the tool actually sends is a real VBR 1.3-rev2 endpoint and that every top-level body field
exists in that endpoint's request model (or one of its subtypes) - catching typos in paths and field names.
"""

import pytest

from conftest import data
from vbr_write_mcp import catalog
from vbr_write_mcp.settings import write_settings

pytestmark = pytest.mark.httpx_mock(assert_all_responses_were_requested=False)

U1 = "0b6b0d2e-7f0c-4f43-9a55-000000000001"
U2 = "0b6b0d2e-7f0c-4f43-9a55-000000000002"
U3 = "0b6b0d2e-7f0c-4f43-9a55-000000000003"
VM = {"platform": "VSphere", "hostName": "vc01", "name": "web-01", "type": "VirtualMachine", "objectId": "vm-42"}
VAPP = {"platform": "CloudDirector", "hostName": "vcd01", "name": "vapp-1", "type": "vApp", "objectId": "urn:vapp"}

CASES: list[tuple[str, dict]] = [
    (
        "api_write",
        {"method": "POST", "path": "/api/v1/highAvailabilityCluster/switchover", "body": {"ignoreLag": True}},
    ),
    ("create_object", {"kind": "credentials", "spec": {"type": "Standard", "username": "corp\\svc", "password": "x"}}),
    ("create_object", {"kind": "job", "spec": {"name": "J", "type": "VSphereBackup"}}),
    ("update_object", {"kind": "job", "id": U1, "changes": {"description": "new"}}),
    ("update_object", {"kind": "user", "id": U1, "changes": {"roles": [{"name": "Veeam Backup Operator"}]}}),
    ("update_object", {"kind": "global_vm_exclusion", "id": U1, "changes": {"note": "n"}}),
    ("delete_object", {"kind": "repository", "id": U1, "options": {"deleteBackups": False}}),
    ("delete_object", {"kind": "protection_group", "id": U1}),
    ("update_settings", {"area": "config_backup", "changes": {"restorePointsToKeep": 30}}),
    ("update_settings", {"area": "repository_access_permissions", "id": U1, "changes": {"accessPolicy": "x"}}),
    ("clone_job", {"job_id": U1}),
    ("start_quick_backup", {"platform": "vsphere", "object": VM}),
    (
        "start_quick_backup",
        {
            "platform": "agent",
            "object": {"platform": "Agent", "id": U1, "name": "pc", "type": "WindowsComputer", "protectionGroupId": U2},
        },
    ),
    ("apply_job_configuration", {"job_id": U1}),
    ("clear_job_cache", {"job_id": U1}),
    ("stop_session", {"session_id": U1}),
    ("stop_task", {"task_id": U1}),
    ("stop_automation_session", {"session_id": U1}),
    ("delete_backup", {"backup_id": U1, "include_gfs": True}),
    ("delete_backup_object", {"backup_id": U1, "object_id": U2}),
    ("move_backup", {"backup_id": U1, "target_repository_id": U2}),
    ("copy_backups", {"kind": "machines", "spec": {"backupIds": [U1]}}),
    ("move_backup_objects_to_job", {"backup_id": U1, "target_job_id": U2, "backup_object_ids": [U3]}),
    ("run_backup_health_check", {"backup_ids": [U1], "repair": True}),
    ("set_backup_file_malware_state", {"backup_id": U1, "backup_file_id": U2, "state": "clean"}),
    ("manage_move_copy_session", {"session_id": U1, "action": "Retry"}),
    ("create_recovery_media", {"backup_object_id": U1}),
    (
        "restore_cloud_director_vm",
        {"restore_point_id": U1, "mode": "OriginalLocation", "power_up": True, "reason": "t"},
    ),
    (
        "restore_cloud_director_vm",
        {
            "restore_point_id": U1,
            "mode": "Customized",
            "new_name": "web-01-r",
            "vapp": VAPP,
            "proxy_ids": [U2],
            "secure_restore": {"antivirusScanEnabled": True},
        },
    ),
    ("restore_hyperv_vm", {"restore_point_id": U1, "mode": "Customized", "destination_host": VM, "new_name": "x"}),
    ("start_instant_recovery_hyperv_vm", {"restore_point_id": U1, "mode": "OtherPlatform", "destination_host": VM}),
    ("finish_instant_recovery_hyperv_vm", {"mount_id": U1, "action": "migrate"}),
    ("finish_instant_recovery_hyperv_vm", {"mount_id": U1, "action": "unmount"}),
    ("start_file_level_restore", {"restore_point_id": U1, "guest_os": "Windows"}),
    ("restore_files", {"session_id": U1, "paths": ["C:\\a.txt"], "restore_type": "Overwrite"}),
    (
        "restore_files",
        {
            "session_id": U1,
            "paths": ["C:\\a.txt"],
            "restore_type": "Keep",
            "destination": "another",
            "target_host": {"type": "VMware"},
            "target_path": "D:\\r",
        },
    ),
    ("unmount_file_level_restore", {"session_id": U1}),
    ("failover_replicas", {"mode": "planned", "replica_point_ids": [U1], "reason": "dr test"}),
    ("failover_replicas", {"mode": "undo", "replica_point_ids": [U1]}),
    (
        "failback_replicas",
        {"action": "start", "replica_point_ids": [U1], "target": "OriginalVM", "switchover": {"type": "Manual"}},
    ),
    ("failback_replicas", {"action": "commit", "replica_point_ids": [U1]}),
    ("failback_replicas", {"action": "switch_to_production", "replica_point_ids": [U1]}),
    ("rescan_replicas", {}),
    ("set_protection_group_enabled", {"protection_group_id": U1, "enabled": False}),
    ("rescan_protection_group", {"protection_group_id": U1}),
    ("manage_discovered_computers", {"protection_group_id": U1, "action": "install_agent", "entity_ids": [U2]}),
    ("manage_discovered_computers", {"protection_group_id": U1, "action": "remove", "entity_ids": [U2]}),
    ("set_agent_backup_attachment", {"discovered_entity_id": U1, "backup_ids": [U2], "attach": True}),
    ("reboot_recovery_appliances", {}),
    ("mark_backup_objects_clean", {"object_ids": [U1], "reason": "false positive"}),
    ("manage_malware_exclusions", {"action": "upsert", "exclusions": [{"type": "ObjectId", "note": "x"}]}),
    ("manage_malware_exclusions", {"action": "delete", "exclusion_ids": [U1]}),
    ("start_encryption_analysis", {"event_id": U1}),
    (
        "report_malware_event",
        {"machine": {"fqdn": "a.corp"}, "details": "d", "engine": "EDR", "detection_time_utc": "2026-11-20T10:00:00Z"},
    ),
    ("set_ioc_monitoring", {"enabled": False, "indicators": ["mimikatz"]}),
    ("install_license", {"license_base64": "aGVsbG8="}),
    ("refresh_license", {"action": "renew"}),
    ("refresh_license", {"action": "update"}),
    ("set_license_options", {"auto_update": True, "proactive_support": True, "unlicensed_agent_consumption": False}),
    ("manage_instance_license", {"instance_id": U1, "action": "assign_server"}),
    ("manage_instance_license", {"instance_id": U1, "action": "remove"}),
    ("remove_installed_license", {"section": "Socket"}),
    (
        "change_credentials_secret",
        {"credentials_id": U1, "what": "linux_private_key", "secret": "KEY", "passphrase": "p"},
    ),
    ("change_credentials_secret", {"credentials_id": U1, "what": "cloud_secret_key", "secret": "S"}),
    ("change_encryption_password", {"encryption_password_id": U1, "new_password": "p", "new_hint": "h"}),
    ("manage_user_security", {"user_id": U1, "action": "reset_mfa"}),
    ("manage_user_security", {"user_id": U1, "action": "enable_service_account"}),
    ("clone_role", {"role_id": U1}),
    ("security_analyzer", {"action": "suppress", "best_practice_id": U1, "note": "accepted risk"}),
    ("security_analyzer", {"action": "start"}),
    ("rescan_managed_servers", {"managed_server_ids": [U1]}),
    ("rescan_inventory", {"hostname": "vc01.corp.local"}),
    ("set_default_mount_server", {"mount_server_id": U1}),
    ("export_configuration", {"kind": "jobs", "filters": {"names": ["J"]}}),
    ("import_configuration", {"kind": "proxies", "collection": {"proxies": [{"name": "p1"}]}}),
    ("export_support_logs", {"scope": "Jobs", "ids": [U1], "date_from": "2026-11-01T00:00:00Z"}),
    ("send_test_email", {}),
    ("high_availability_cluster", {"action": "failover"}),
]


def _allowed_fields(schema: dict | None) -> set[str] | None:
    if not schema or "$ref" not in schema:
        return None
    models = catalog.models()
    names = [schema["$ref"], *((models.get(schema["$ref"]) or {}).get("x-subtypes") or [])]
    fields: set[str] = set()
    for n in names:
        fields |= set(((models.get(n) or {}).get("properties") or {}).keys())
    return fields


@pytest.mark.parametrize(("tool", "args"), CASES, ids=[f"{t}-{i}" for i, (t, _) in enumerate(CASES)])
async def test_tool_sends_valid_requests(client, vbr, tool, args):
    write_settings.confirmation_mode = "none"
    vbr.objects["/api/v1/jobs/" + U1] = {"id": U1, "name": "Job A", "description": "old", "type": "VSphereBackup"}
    result = data(await client.call_tool(tool, args))
    assert result["status"] == "submitted", result
    assert vbr.writes, "tool sent no state-changing request"
    for method, path, body, _params in vbr.writes:
        entry = catalog.match(method, path)
        assert entry is not None, f"{method} {path} is not a VBR 1.3-rev2 endpoint"
        allowed = _allowed_fields(entry["body"])
        if allowed is not None and isinstance(body, dict):
            unknown = set(body) - allowed
            assert not unknown, f"{method} {path}: fields {unknown} not in {entry['body']['$ref']}"


def test_every_write_tool_has_a_case():
    import asyncio

    from conftest import MCP

    tools = asyncio.run(MCP.list_tools())
    writable = {t.name for t in tools if "core" not in t.tags and not (t.annotations and t.annotations.read_only_hint)}
    assert writable - {c[0] for c in CASES} == set()


def test_object_kinds_and_settings_exist_in_catalog():
    from vbr_write_mcp.tools.objects import KINDS, SETTINGS

    for kind, k in KINDS.items():
        assert catalog.match("POST", k.path), kind
        if k.create_model:
            assert k.create_model in catalog.models(), (kind, k.create_model)
        if k.update:
            assert catalog.match("PUT", k.update_path.format(base=k.path, id=U1)), kind
        if k.delete:
            entry = catalog.match("DELETE", f"{k.path}/{U1}")
            assert entry, kind
            assert set(k.delete_params) <= set(entry["query"]), kind
    for area, path in SETTINGS.items():
        concrete = path.format(id=U1)
        assert catalog.match("GET", concrete) and catalog.match("PUT", concrete), area
