"""End-to-end over stdio with a 2025-06-18 (handshake-era) client: server-initiated elicitation path."""

import json
import os
import subprocess
import sys

env = {
    **os.environ,
    "VBR_BASE_URL": "https://127.0.0.1:1",
    "VBR_USERNAME": "x",
    "VBR_PASSWORD": "x",
    "VBRW_DRY_RUN": "true",
    "VBRW_AUDIT_LOG": "",
    "FASTMCP_SHOW_SERVER_BANNER": "false",
    "FASTMCP_LOG_LEVEL": "ERROR",
}
p = subprocess.Popen(
    [sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True
)


def send(msg):
    p.stdin.write(json.dumps(msg) + "\n")
    p.stdin.flush()


def recv():
    return json.loads(p.stdout.readline())


send(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"elicitation": {}},
            "clientInfo": {"name": "legacy", "version": "1"},
        },
    }
)
init = recv()
print("protocol:", init["result"]["protocolVersion"])
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
print("tools:", len(recv()["result"]["tools"]))
for answer in (True, False):
    send(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "stop_session", "arguments": {"session_id": "0b6b0d2e-7f0c-4f43-9a55-000000000001"}},
        }
    )
    while True:
        m = recv()
        if m.get("method") == "elicitation/create":
            print("elicitation asked:", m["params"]["message"].splitlines()[1][:60])
            send({"jsonrpc": "2.0", "id": m["id"], "result": {"action": "accept", "content": {"value": answer}}})
        elif m.get("id") == 3:
            print("answer", answer, "->", json.loads(m["result"]["content"][0]["text"])["status"])
            break
p.terminate()
