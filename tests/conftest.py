import json
import os
from pathlib import Path

os.environ["ENV_FILE_OVERRIDE"] = str(Path(__file__).parent / ".env.test")
for _k in [k for k in os.environ if k.startswith(("VBR_", "VBRW_", "MCP_"))]:
    del os.environ[_k]

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastmcp import Client  # noqa: E402

import vbr_write_mcp  # noqa: E402
from vbr_write_mcp import safety  # noqa: E402
from vbr_write_mcp.settings import write_settings  # noqa: E402

MCP = vbr_write_mcp.build()
BASE = "https://vbr.example.com:9419"


@pytest.fixture(autouse=True)
def _reset(tmp_path):
    from vbr_write_mcp.server import vbr_client

    vbr_client._access_token = None
    vbr_client._refresh_token = None
    saved = write_settings.model_dump()
    write_settings.audit_log = str(tmp_path / "audit.jsonl")
    safety._ISSUED.clear()
    yield
    for k, v in saved.items():
        setattr(write_settings, k, v)


class FakeVbr:
    """Answers every request: token, GETs from `objects` (or a generic object), and records every write."""

    def __init__(self) -> None:
        self.objects: dict[str, object] = {}
        self.writes: list[tuple[str, str, object, dict]] = []
        self.gets: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "refresh_token": "r"})
        if request.method == "GET":
            self.gets.append(path)
            last = path.rsplit("/", 1)[-1]
            generic = {"id": last, "name": f"obj-{last[:8]}"} if len(last) == 36 and last.count("-") == 4 else {}
            obj = self.objects.get(path, generic)
            return httpx.Response(200, json=obj)
        body = json.loads(request.content) if request.content else None
        self.writes.append((request.method, path, body, dict(request.url.params)))
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(201, json={"id": "11111111-1111-1111-1111-111111111111", "state": "Starting"})


@pytest.fixture
def vbr(httpx_mock) -> FakeVbr:
    fake = FakeVbr()
    httpx_mock.add_callback(fake, is_reusable=True)
    return fake


@pytest.fixture
async def client():
    async with Client(MCP) as c:
        yield c


def data(result):
    content = result.structured_content
    if content is None and result.content:
        return json.loads(result.content[0].text)
    if isinstance(content, dict) and content.keys() == {"result"}:
        return content["result"]
    return content
