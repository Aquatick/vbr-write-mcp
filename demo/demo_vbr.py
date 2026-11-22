"""A demo VBR server: try vbr-write-mcp (and Veeam's vbr-mcp) without touching a real backup server.

Reads are answered from Veeam's own integration-test stand (`stand.json.gz`, dumped from vbr-mcp's
`integration_tests/stand`: jobs, sessions, backups, repositories, malware events, license ...) through Veeam's mock
store (`vbr_mock/`, MIT). On top of that, this server accepts every write endpoint of the VBR 1.3-rev2 API
(POST/PUT/DELETE), records it and answers like VBR: an action returns a session that finishes successfully, a PUT
replaces what the object reads like afterwards, a DELETE removes it.

    uv run python demo/demo_vbr.py                 # http://127.0.0.1:9419, login demo / demo
    open http://127.0.0.1:9419/_demo                # live view of every write VBR received

Point the MCP servers at it with VBR_BASE_URL=http://127.0.0.1:9419, VBR_USERNAME=demo, VBR_PASSWORD=demo.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response  # noqa: E402
from vbr_mock.models import MockEndpoint  # noqa: E402
from vbr_mock.presenters import MockedError  # noqa: E402
from vbr_mock.store import EnvironmentStore, MethodNotAllowedError, RouteNotFoundError  # noqa: E402

from vbr_write_mcp import catalog  # noqa: E402

ENV = "demo"
USERNAME = PASSWORD = "demo"
TOKEN = "demo-token"


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- extra demo data the stand does not have: replicas and a Cloud Director VM -----------------------------------

_EXTRA: dict[str, Any] = {
    "/api/v1/replicas": [
        {
            "id": "70000000-0000-0000-0000-000000000001",
            "name": "web-01_replica",
            "jobId": "10000000-0000-0000-0000-000000000001",
            "platformName": "VMware",
            "state": "Ready",
            "creationTime": "2026-10-01T22:00:00Z",
        },
        {
            "id": "70000000-0000-0000-0000-000000000002",
            "name": "sql-prod-01_replica",
            "jobId": "10000000-0000-0000-0000-000000000002",
            "platformName": "VMware",
            "state": "Ready",
            "creationTime": "2026-10-01T22:30:00Z",
        },
    ],
    "/api/v1/replicas/70000000-0000-0000-0000-000000000001/replicaPoints": [
        {
            "id": "71000000-0000-0000-0000-000000000001",
            "name": "web-01",
            "replicaId": "70000000-0000-0000-0000-000000000001",
            "creationTime": "2026-10-05T22:05:00Z",
            "platformName": "VMware",
        },
    ],
    "/api/v1/replicaPoints/71000000-0000-0000-0000-000000000001": {
        "id": "71000000-0000-0000-0000-000000000001",
        "name": "web-01",
        "replicaId": "70000000-0000-0000-0000-000000000001",
        "creationTime": "2026-10-05T22:05:00Z",
        "platformName": "VMware",
    },
    "/api/v1/security/users/73000000-0000-0000-0000-000000000001": {
        "id": "73000000-0000-0000-0000-000000000001",
        "name": "LAB\\backup-ops",
        "type": "ExternalGroup",
        "isServiceAccount": False,
    },
    "/api/v1/security/users/73000000-0000-0000-0000-000000000001/roles": {
        "roles": [{"id": "74000000-0000-0000-0000-000000000001", "name": "Veeam Backup Viewer"}]
    },
    "/api/v1/backupInfrastructure/repositories/20000000-0000-0000-0000-000000000001/accessPermissions": {
        "accessPolicy": "AllowAll",
        "encryptionSettings": {"isEnabled": False},
    },
    "/api/v1/restorePoints/72000000-0000-0000-0000-000000000001": {
        "id": "72000000-0000-0000-0000-000000000001",
        "name": "tenant-a-web-01",
        "platformName": "CloudDirector",
        "creationTime": "2026-10-05T23:10:00Z",
        "backupId": "52000000-0000-0000-0000-000000000001",
    },
}


def build_store() -> EnvironmentStore:
    store = EnvironmentStore()
    with gzip.open(HERE / "stand.json.gz", "rt", encoding="utf-8") as f:
        for e in json.load(f):
            store.put(
                ENV,
                MockEndpoint(
                    route=e["route"],
                    method=e["method"],
                    presenter=e["presenter"],
                    data=e["data"],
                    hidden=frozenset(e["hidden"]),
                    status_code=e["status_code"],
                ),
            )
    for route, data in _EXTRA.items():
        store.put(
            ENV, MockEndpoint(route=route, presenter="vbr-page" if isinstance(data, list) else "as-is", data=data)
        )
    return store


# --- the app -------------------------------------------------------------------------------------------------------


class Demo:
    def __init__(self) -> None:
        self.store = build_store()
        self.writes: list[dict[str, Any]] = []
        self.deleted: set[str] = set()

    def put_get(self, route: str, data: Any) -> None:
        self.store.put(ENV, MockEndpoint(route=route, method="GET", presenter="as-is", data=data))

    def session(self, entry: dict[str, Any]) -> dict[str, Any]:
        sid = str(uuid.uuid4())
        name = entry["summary"]
        started = {
            "id": sid,
            "name": name,
            "jobId": "00000000-0000-0000-0000-000000000000",
            "sessionType": "Restore" if "restore" in entry["path"].lower() else "Infrastructure",
            "creationTime": _now(),
            "state": "Working",
            "usn": len(self.writes),
            "progressPercent": 0,
            "result": {"result": "None", "isCanceled": False},
        }
        done = {
            **started,
            "state": "Stopped",
            "endTime": _now(),
            "progressPercent": 100,
            "result": {"result": "Success", "message": "Demo VBR: accepted and completed.", "isCanceled": False},
        }
        self.put_get(f"/api/v1/sessions/{sid}", done)
        self.store.put(
            ENV,
            MockEndpoint(
                route=f"/api/v1/sessions/{sid}/logs",
                method="GET",
                presenter="as-is",
                data={
                    "records": [
                        {
                            "id": 1,
                            "status": "Succeeded",
                            "title": f"{name} (demo)",
                            "startTime": _now(),
                            "updateTime": _now(),
                        }
                    ],
                    "totalRecords": 1,
                },
            ),
        )
        return started


_READ_POSTS = ("Get ", "Browse", "Search", "View ", "Compare", "Check ", "Validate", "Verify", "Request TLS")

demo = Demo()
app = FastAPI(title="Demo VBR")


@app.post("/api/oauth2/token")
async def token(request: Request):
    form = await request.form()
    if form.get("grant_type") == "refresh_token" or (
        form.get("username") == USERNAME and form.get("password") == PASSWORD
    ):
        return {"access_token": TOKEN, "refresh_token": TOKEN, "expires_in": 3600, "token_type": "bearer"}
    return JSONResponse({"error": "invalid_grant", "error_description": "Invalid credentials"}, 400)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/_demo")


@app.get("/_demo", response_class=HTMLResponse)
async def dashboard():
    rows = (
        "".join(
            f"<tr><td>{html.escape(w['time'][11:19])}</td><td class=m-{w['method']}>{w['method']}</td>"
            f"<td><code>{html.escape(w['path'])}</code><div class=s>{html.escape(w['summary'])}</div></td>"
            f"<td><pre>{html.escape(json.dumps(w['body'], indent=1, ensure_ascii=False)[:1500]) if w['body'] is not None else ''}</pre></td></tr>"
            for w in reversed(demo.writes)
        )
        or "<tr><td colspan=4 class=empty>Henüz yazma isteği yok. MCP'den bir tool çağırın.</td></tr>"
    )
    return f"""<!doctype html><html lang=tr><head><meta charset=utf-8><meta http-equiv=refresh content=2>
<title>Demo VBR - gelen yazma istekleri</title><style>
body{{font:14px system-ui,sans-serif;margin:24px;background:#f6f7f9;color:#1d2430}}h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:#5b6573;margin-bottom:16px}}table{{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden}}
td,th{{padding:8px 10px;border-bottom:1px solid #e6e8eb;vertical-align:top;text-align:left}}th{{background:#eef0f3;font-weight:600}}
pre{{margin:0;font-size:12px;white-space:pre-wrap;max-width:560px}}.s{{color:#5b6573;font-size:12px}}.empty{{color:#8a93a0;text-align:center;padding:32px}}
.m-POST{{color:#0a7d32;font-weight:600}}.m-PUT{{color:#a35d00;font-weight:600}}.m-DELETE{{color:#c0262d;font-weight:600}}
</style></head><body><h1>Demo VBR &mdash; VBR'a ulaşan yazma istekleri</h1>
<div class=sub>{len(demo.writes)} istek &middot; sayfa 2 saniyede bir yenilenir &middot; okuma istekleri gösterilmez</div>
<table><tr><th>Saat</th><th>Method</th><th>Endpoint</th><th>Gövde</th></tr>{rows}</table></body></html>"""


@app.get("/_demo/requests")
async def recorded():
    return demo.writes


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def vbr(full_path: str, request: Request):
    if request.headers.get("authorization", "") != f"Bearer {TOKEN}":
        return JSONResponse({"errorCode": "Unauthorized", "message": "The access token is missing or invalid."}, 401)
    path = "/" + full_path
    method = request.method
    raw = await request.body()
    body = json.loads(raw) if raw else None
    query = dict(request.query_params)
    if method == "GET" and path in demo.deleted:
        return JSONResponse({"errorCode": "NotFound", "message": f"The requested resource {path} was not found."}, 404)
    entry = catalog.match(method, path)
    # VBR serves some reads over POST (inventory browser, network info ...); every other POST is an action, even on a
    # path the stand serves verb-less for its GET (POST /api/v1/jobs creates a job, it does not list them).
    post_read = method == "POST" and (entry is None or entry["summary"].startswith(_READ_POSTS))
    if method == "GET" or post_read:
        try:
            served = demo.store.serve(ENV, path, query, method, body)
            return (
                Response(status_code=served.status_code)
                if served.body is None
                else JSONResponse(served.body, served.status_code)
            )
        except MockedError as e:
            return JSONResponse({"errorCode": "Error", "message": e.message}, e.status_code)
        except (RouteNotFoundError, MethodNotAllowedError):
            if method == "GET":
                return JSONResponse(
                    {"errorCode": "NotFound", "message": f"The requested resource {path} was not found."}, 404
                )
    if entry is None:
        return JSONResponse({"errorCode": "NotFound", "message": f"{method} {path} is not a VBR endpoint."}, 404)
    demo.writes.append(
        {"time": _now(), "method": method, "path": path, "summary": entry["summary"], "body": body, "query": query}
    )
    if method == "PUT":
        demo.deleted.discard(path)
        demo.put_get(path, body)
        return JSONResponse(body, 200)
    if method == "DELETE":
        demo.deleted.add(path)
        return Response(status_code=204)
    return JSONResponse(demo.session(entry), 201)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9419)
    args = parser.parse_args()
    print(
        f"Demo VBR: http://{args.host}:{args.port}  (login {USERNAME}/{PASSWORD})  -  writes: http://{args.host}:{args.port}/_demo"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
