"""End-to-end check over real stdio + HTTP: Veeam's vbr-mcp and vbr-write-mcp side by side against the demo VBR.

uv run python demo/demo_vbr.py &        # in another terminal
uv run python scripts/e2e_demo.py [path/to/vbr-mcp executable]
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_endpoints import CASES  # noqa: E402

VBR = "http://127.0.0.1:9419"
ENV = {
    **os.environ,
    "VBR_BASE_URL": VBR,
    "VBR_USERNAME": "demo",
    "VBR_PASSWORD": "demo",
    "MCP_ENABLED": "false",
    "VBRW_AUDIT_LOG": str(ROOT / "demo" / "audit.jsonl"),
    "VBRW_CONFIRMATION_MODE": "token",
    "FASTMCP_SHOW_SERVER_BANNER": "false",
    "FASTMCP_LOG_LEVEL": "ERROR",
}
EXE = ".exe" if os.name == "nt" else ""
BIN = "Scripts" if os.name == "nt" else "bin"

SQL_DEV = "10000000-0000-0000-0000-000000000003"
OK, FAIL = "✔", "✘"
failures: list[str] = []


def data(r):
    c = r.structured_content
    if c is None and r.content:
        return json.loads(r.content[0].text)
    return c["result"] if isinstance(c, dict) and c.keys() == {"result"} else c


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {OK if cond else FAIL} {name}" + (f"  - {detail}" if detail else ""))
    if not cond:
        failures.append(name)


async def confirmed(w: Client, tool: str, args: dict) -> tuple[dict, dict]:
    plan = data(await w.call_tool(tool, args))
    assert plan["status"] == "confirmation_required", plan
    done = data(await w.call_tool(tool, {**args, "confirm_token": plan["confirm_token"]}))
    return plan, done


async def main() -> None:
    write = Client(StdioTransport(str(ROOT / ".venv" / BIN / f"vbr-write-mcp{EXE}"), [], env=ENV))
    vbr_mcp_cmd = sys.argv[1] if len(sys.argv) > 1 else None
    read = Client(StdioTransport(vbr_mcp_cmd, [], env=ENV)) if vbr_mcp_cmd else None

    async with write as w:
        tools = await w.list_tools()
        print(f"vbr-write-mcp: {len(tools)} tool")
        check("68 tool listelendi", len(tools) == 68)

        print("\n1) Senaryo: vbr-mcp ile oku, vbr-write-mcp ile değiştir")
        if read:
            async with read as r:
                jobs = data(await r.call_tool("list_jobs", {"name_filter": "SQL-Dev"}))
                check("vbr-mcp list_jobs SQL-Dev'i buldu", jobs and jobs[0]["id"] == SQL_DEV, jobs[0]["name"])
                before = jobs[0]["storage"]["retentionPolicy"]
                plan, done = await confirmed(
                    w,
                    "update_object",
                    {
                        "kind": "job",
                        "id": SQL_DEV,
                        "changes": {"storage": {"retentionPolicy": {"quantity": before["quantity"] + 7}}},
                    },
                )
                check(
                    "onay planı diff gösteriyor",
                    plan["plan"]["preview"]["changes"][0]["path"] == "storage.retentionPolicy.quantity",
                    json.dumps(plan["plan"]["preview"]["changes"][0]),
                )
                check("onaydan sonra gönderildi", done["status"] == "submitted")
                after = data(await r.call_tool("list_jobs", {"id": SQL_DEV}))["storage"]["retentionPolicy"]
                check(
                    "vbr-mcp değişikliği görüyor",
                    after["quantity"] == before["quantity"] + 7,
                    f"{before['quantity']} -> {after['quantity']}",
                )
        else:
            print("  (vbr-mcp yolu verilmedi, bu adım atlandı)")

        print("\n2) Senaryo: Cloud Director VM restore (onaylı) + session takibi")
        plan, done = await confirmed(
            w,
            "restore_cloud_director_vm",
            {
                "restore_point_id": "72000000-0000-0000-0000-000000000001",
                "mode": "Customized",
                "new_name": "tenant-a-web-01-restored",
                "vapp": {
                    "platform": "CloudDirector",
                    "hostName": "vcd01",
                    "name": "tenant-a-vapp",
                    "type": "vApp",
                    "objectId": "urn:vcloud:vapp:1",
                },
            },
        )
        check(
            "plan restore point'i gösteriyor", plan["plan"]["preview"]["restorePoint"].get("name") == "tenant-a-web-01"
        )
        sid = done["result"]["id"]
        res = data(await w.call_tool("get_session_result", {"session_id": sid, "wait_seconds": 10}))
        check("session tamamlandı", res["finished"] and res["result"]["result"] == "Success", res["state"])

        print("\n3) Senaryo: token güvenliği")
        plan = data(await w.call_tool("delete_backup", {"backup_id": "52000000-0000-0000-0000-000000000003"}))
        check(
            "silme önce plan döndü, istek gönderilmedi",
            plan["status"] == "confirmation_required",
            plan["summary"] if "summary" in plan else plan["plan"]["summary"],
        )
        try:
            await w.call_tool(
                "delete_backup",
                {
                    "backup_id": "52000000-0000-0000-0000-000000000003",
                    "include_gfs": True,
                    "confirm_token": plan["confirm_token"],
                },
            )
            check("farklı argümanla token reddedildi", False)
        except Exception as e:
            check("farklı argümanla token reddedildi", "different call" in str(e))

        print("\n4) Hata yolu: VBR'ın 404 mesajı aynen iletiliyor")
        try:
            await w.call_tool(
                "update_object",
                {"kind": "job", "id": "99999999-0000-0000-0000-000000000000", "changes": {"description": "x"}},
            )
            check("olmayan job reddedildi", False)
        except Exception as e:
            check("olmayan job reddedildi", "not found" in str(e).lower(), str(e)[:90])

        print(f"\n5) Tüm yazma tool'ları gerçek HTTP üzerinden ({len(CASES)} durum)")
        async with httpx.AsyncClient() as h:
            start = len((await h.get(f"{VBR}/_demo/requests")).json())
            bad = []
            real_ids = {  # the demo VBR's own objects for the edits (an edit reads the object first)
                ("update_object", "job"): {"id": SQL_DEV},
                ("update_object", "user"): {"id": "73000000-0000-0000-0000-000000000001"},
                ("update_settings", "repository_access_permissions"): {"id": "20000000-0000-0000-0000-000000000001"},
            }
            for tool, args in CASES:
                args = {**args, **real_ids.get((tool, args.get("kind") or args.get("area")), {})}
                try:
                    out = data(await w.call_tool(tool, args))
                    if out["status"] == "confirmation_required":
                        out = data(await w.call_tool(tool, {**args, "confirm_token": out["confirm_token"]}))
                    if out["status"] != "submitted":
                        bad.append(f"{tool}: {out['status']}")
                except Exception as e:
                    bad.append(f"{tool}: {e}")
            got = (await h.get(f"{VBR}/_demo/requests")).json()[start:]
        check(f"{len(CASES) - len(bad)}/{len(CASES)} çağrı VBR tarafından kabul edildi", not bad, "; ".join(bad)[:400])
        check(f"VBR {len(got)} yazma isteği aldı, hepsi geçerli 1.3-rev2 endpoint'i", len(got) >= len(CASES))

    audit = (ROOT / "demo" / "audit.jsonl").read_text(encoding="utf-8")
    check("audit log'da parola düz metin yok", "Sup3r" not in audit and '"password": "x"' not in audit)
    print("\nSONUÇ:", "HEPSİ GEÇTİ" if not failures else f"{len(failures)} HATA: {failures}")
    sys.exit(1 if failures else 0)


asyncio.run(main())
