import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError

from conftest import MCP, data
from vbr_write_mcp.settings import write_settings

pytestmark = pytest.mark.httpx_mock(assert_all_responses_were_requested=False)

U1 = "0b6b0d2e-7f0c-4f43-9a55-000000000001"
U2 = "0b6b0d2e-7f0c-4f43-9a55-000000000002"


async def test_destructive_call_returns_plan_and_sends_nothing(client, vbr):
    write_settings.confirmation_mode = "token"
    out = data(await client.call_tool("delete_backup", {"backup_id": U1}))
    assert out["status"] == "confirmation_required"
    assert out["confirm_token"].startswith("cfm_")
    assert out["plan"]["requests"] == [
        {"method": "DELETE", "path": f"/api/v1/backups/{U1}", "params": {"includeGFS": False}}
    ]
    assert any("irreversible" in i for i in out["plan"]["impact"])
    assert vbr.writes == []


async def test_token_executes_once_and_only_for_the_same_arguments(client, vbr):
    write_settings.confirmation_mode = "token"
    plan = data(await client.call_tool("delete_backup", {"backup_id": U1}))
    token = plan["confirm_token"]

    with pytest.raises(ToolError, match="different call"):
        await client.call_tool("delete_backup", {"backup_id": U1, "include_gfs": True, "confirm_token": token})
    # a mismatched attempt burns the token
    with pytest.raises(ToolError, match="unknown, already used or expired"):
        await client.call_tool("delete_backup", {"backup_id": U1, "confirm_token": token})
    assert vbr.writes == []

    token = data(await client.call_tool("delete_backup", {"backup_id": U1}))["confirm_token"]
    done = data(await client.call_tool("delete_backup", {"backup_id": U1, "confirm_token": token}))
    assert done["status"] == "submitted"
    assert [(m, p) for m, p, _, _ in vbr.writes] == [("DELETE", f"/api/v1/backups/{U1}")]
    with pytest.raises(ToolError, match="already used"):
        await client.call_tool("delete_backup", {"backup_id": U1, "confirm_token": token})


async def test_token_is_bound_to_the_tool(client, vbr):
    write_settings.confirmation_mode = "token"
    token = data(await client.call_tool("stop_session", {"session_id": U1}))["confirm_token"]
    with pytest.raises(ToolError, match="different call"):
        await client.call_tool("stop_task", {"task_id": U1, "confirm_token": token})


async def test_write_tools_run_without_confirmation_by_default(client, vbr):
    write_settings.confirmation_mode = "token"
    out = data(await client.call_tool("rescan_replicas", {}))
    assert out["status"] == "submitted"
    write_settings.confirm_writes = True
    out = data(await client.call_tool("rescan_replicas", {}))
    assert out["status"] == "confirmation_required"


async def test_elicitation_accept_and_decline(vbr):
    write_settings.confirmation_mode = "auto"
    seen = []

    async def accept(message, response_type, params, context):
        seen.append(message)
        return True

    async with Client(MCP, elicitation_handler=accept) as c:
        out = data(await c.call_tool("stop_session", {"session_id": U1}))
    assert out["status"] == "submitted"
    assert "Stop session" in seen[0]
    assert len(vbr.writes) == 1

    async def decline(message, response_type, params, context):
        return ElicitResult(action="decline")

    async with Client(MCP, elicitation_handler=decline) as c:
        out = data(await c.call_tool("stop_session", {"session_id": U1}))
    assert out["status"] == "cancelled"
    assert len(vbr.writes) == 1

    async def unticked(message, response_type, params, context):
        return False

    async with Client(MCP, elicitation_handler=unticked) as c:
        out = data(await c.call_tool("stop_session", {"session_id": U1}))
    assert out["status"] == "cancelled"


async def test_auto_mode_falls_back_to_token_without_elicitation(client, vbr):
    write_settings.confirmation_mode = "auto"
    out = data(await client.call_tool("stop_session", {"session_id": U1}))
    assert out["status"] == "confirmation_required"


async def test_dry_run_sends_nothing(client, vbr):
    write_settings.confirmation_mode = "none"
    write_settings.dry_run = True
    out = data(await client.call_tool("failover_replicas", {"mode": "now", "replica_point_ids": [U1]}))
    assert out["status"] == "dry_run"
    assert out["plan"]["requests"][0]["path"] == "/api/v1/failover/failoverNow/vSphere/snapshot"
    assert vbr.writes == []


async def test_access_level_refuses_destructive_variant_of_mixed_tool(client, vbr):
    write_settings.access_level = "write"
    out = data(await client.call_tool("set_protection_group_enabled", {"protection_group_id": U1, "enabled": True}))
    assert out["status"] == "submitted"
    with pytest.raises(ToolError, match="VBRW_ACCESS_LEVEL=write"):
        await client.call_tool("set_protection_group_enabled", {"protection_group_id": U1, "enabled": False})


def test_access_level_write_hides_destructive_tools():
    env = {**os.environ, "VBRW_ACCESS_LEVEL": "write", "VBRW_TOOL_GROUPS": "jobs,restore"}
    code = (
        "import asyncio, vbr_write_mcp; m = vbr_write_mcp.build(); "
        "print(','.join(sorted(t.name for t in asyncio.run(m.list_tools()))))"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    names = set(out.stdout.strip().split(","))
    assert "delete_backup" not in names and "restore_cloud_director_vm" not in names and "api_write" not in names
    assert {
        "clone_job",
        "start_quick_backup",
        "start_file_level_restore",
        "restore_files",
        "describe_endpoint",
    } <= names
    assert "failover_replicas" not in names  # group not enabled


async def test_update_object_merges_and_shows_diff(client, vbr):
    write_settings.confirmation_mode = "token"
    job = {
        "id": U1,
        "name": "SQL",
        "type": "VSphereBackup",
        "storage": {"retentionPolicy": {"quantity": 7, "type": "Days"}},
    }
    vbr.objects[f"/api/v1/jobs/{U1}"] = job
    changes = {"storage": {"retentionPolicy": {"quantity": 14}}}
    plan = data(await client.call_tool("update_object", {"kind": "job", "id": U1, "changes": changes}))
    assert plan["plan"]["preview"]["changes"] == [{"path": "storage.retentionPolicy.quantity", "old": 7, "new": 14}]
    await client.call_tool(
        "update_object", {"kind": "job", "id": U1, "changes": changes, "confirm_token": plan["confirm_token"]}
    )
    method, path, body, _ = vbr.writes[0]
    assert (method, path) == ("PUT", f"/api/v1/jobs/{U1}")
    assert body["storage"]["retentionPolicy"] == {"quantity": 14, "type": "Days"} and body["name"] == "SQL"


async def test_update_object_token_invalid_after_object_changed(client, vbr):
    write_settings.confirmation_mode = "token"
    vbr.objects[f"/api/v1/jobs/{U1}"] = {"id": U1, "name": "SQL", "description": "a"}
    token = data(await client.call_tool("update_object", {"kind": "job", "id": U1, "changes": {"description": "b"}}))[
        "confirm_token"
    ]
    vbr.objects[f"/api/v1/jobs/{U1}"] = {"id": U1, "name": "SQL-renamed", "description": "a"}
    with pytest.raises(ToolError, match="changed since the plan"):
        await client.call_tool(
            "update_object", {"kind": "job", "id": U1, "changes": {"description": "b"}, "confirm_token": token}
        )
    assert vbr.writes == []


async def test_update_without_change_sends_nothing(client, vbr):
    vbr.objects["/api/v1/configBackup"] = {"isEnabled": True, "restorePointsToKeep": 10}
    out = data(
        await client.call_tool("update_settings", {"area": "config_backup", "changes": {"restorePointsToKeep": 10}})
    )
    assert out["status"] == "no_change"
    assert vbr.writes == []


async def test_api_write_refuses_unknown_and_blocked_endpoints(client, vbr):
    with pytest.raises(ToolError, match="not an endpoint"):
        await client.call_tool("api_write", {"method": "POST", "path": "/api/v1/jobs/x/explode"})
    with pytest.raises(ToolError, match="must start with /api/v1/"):
        await client.call_tool("api_write", {"method": "POST", "path": "/api/oauth2/logout"})
    write_settings.blocked_paths = [r"^/api/v1/highAvailabilityCluster"]
    with pytest.raises(ToolError, match="blocked"):
        await client.call_tool("api_write", {"method": "POST", "path": "/api/v1/highAvailabilityCluster/failover"})


async def test_audit_log_redacts_secrets(client, vbr):
    write_settings.confirmation_mode = "none"
    await client.call_tool(
        "create_object",
        {"kind": "credentials", "spec": {"type": "Standard", "username": "svc", "password": "Sup3rSecret!"}},
    )
    text = Path(write_settings.audit_log).read_text()
    assert "Sup3rSecret!" not in text
    record = json.loads(text.splitlines()[-1])
    assert record["status"] == "executed" and record["requests"][0]["body"]["password"] == "***"


async def test_plan_redacts_secrets(client, vbr):
    write_settings.confirmation_mode = "token"
    out = data(
        await client.call_tool(
            "change_credentials_secret", {"credentials_id": U1, "what": "password", "secret": "N3wPass"}
        )
    )
    assert "N3wPass" not in json.dumps(out)


async def test_vbr_error_is_reported_and_audited(client, httpx_mock):
    import httpx

    def fake(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth2/token":
            return httpx.Response(200, json={"access_token": "t"})
        if request.method == "GET":
            return httpx.Response(200, json={})
        return httpx.Response(400, json={"errorCode": "BadRequest", "message": "Job is running."})

    httpx_mock.add_callback(fake, is_reusable=True)
    write_settings.confirmation_mode = "none"
    with pytest.raises(ToolError, match="Job is running"):
        await client.call_tool("clone_job", {"job_id": U1})
    record = json.loads(Path(write_settings.audit_log).read_text().splitlines()[-1])
    assert record["status"] == "failed"


async def test_describe_endpoint_and_model(client):
    ep = data(
        await client.call_tool(
            "describe_endpoint", {"query": "/api/v1/restore/vmRestore/cloudDirector", "method": "POST"}
        )
    )
    assert ep["body"]["$model"] == "EntireCloudDirectorVMRestoreSpec"
    assert "EntireCloudDirectorVMCustomizedRestoreSpec" in ep["body"]["x-subtypes"]
    hits = data(await client.call_tool("describe_endpoint", {"query": "failover"}))
    assert any(h["path"] == "/api/v1/failover/plannedFailover/vSphere/snapshot" for h in hits)
    model = data(await client.call_tool("describe_model", {"name": "EntireCloudDirectorVMCustomizedRestoreSpec"}))
    assert "location" in model["properties"]


async def test_session_result_polls_until_stopped(client, httpx_mock, monkeypatch):
    import asyncio

    import httpx

    states = iter(["Working", "Working", "Stopped"])

    def fake(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth2/token":
            return httpx.Response(200, json={"access_token": "t"})
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"records": [{"title": "x"}]})
        return httpx.Response(200, json={"id": U1, "state": next(states), "result": {"result": "Warning"}})

    httpx_mock.add_callback(fake, is_reusable=True)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))
    out = data(await client.call_tool("get_session_result", {"session_id": U1, "wait_seconds": 60}))
    assert out["finished"] is True and out["logs"] == [{"title": "x"}]
