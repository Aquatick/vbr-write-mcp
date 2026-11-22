"""The one path every state-changing call goes through.

`execute` turns a tool call into a *plan* (what will be sent, to what, with which consequences), gets it confirmed
when the action's level requires it, honors dry-run mode, sends the requests and writes an audit record.

Confirmation is enforced by the server, not left to the client:

* **elicit** - the server asks the user directly through the MCP client (MCP elicitation). The agent cannot answer
  for the user.
* **token** - the first call returns the plan and a one-time `confirm_token` bound to the exact arguments (and, for
  edits, to the current state of the object). The action runs only when the same tool is called again with the same
  arguments and that token, before it expires. The agent is instructed to show the plan to the user and wait for an
  explicit yes in between.
* **auto** - elicit when the client supports it, token otherwise.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic_core import to_jsonable_python

from vbr_write_mcp.settings import write_settings


class Level(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


@dataclass
class Request:
    method: str
    path: str
    body: Any = None
    params: dict[str, Any] | None = None
    timeout: float | None = None
    label: str | None = None

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"method": self.method, "path": self.path}
        if self.params:
            out["params"] = redact(self.params)
        if self.body is not None:
            out["body"] = redact(to_jsonable_python(self.body))
        if self.label:
            out["label"] = self.label
        return out


@dataclass
class Plan:
    tool: str
    level: Level
    summary: str
    requests: list[Request]
    impact: list[str] = field(default_factory=list)
    preview: Any = None

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tool": self.tool,
            "level": self.level.value,
            "summary": self.summary,
            "requests": [r.describe() for r in self.requests],
        }
        if self.impact:
            out["impact"] = self.impact
        if self.preview is not None:
            out["preview"] = self.preview
        return out

    def render(self) -> str:
        lines = [f"[{self.level.value.upper()}] {self.summary}"]
        lines += [f"  - {i}" for i in self.impact]
        for r in self.requests:
            lines.append(f"  {r.method} {r.path}" + (f" ({r.label})" if r.label else ""))
        if self.preview is not None:
            text = json.dumps(self.preview, ensure_ascii=False, default=str)
            lines.append("  Details: " + (text if len(text) < 1500 else text[:1500] + " ..."))
        return "\n".join(lines)


# --- redaction --------------------------------------------------------------------------------------------------

_SECRET_KEY = re.compile(
    r"(password|passphrase|secret|privatekey|private_key|rootpassword|apikey|accesskey|secretkey|token|license$|"
    r"certificate$|pfx|sshkey)",
    re.IGNORECASE,
)


def redact(value: Any) -> Any:
    """A copy of `value` with secret-looking fields replaced by '***'."""
    if isinstance(value, dict):
        return {
            k: ("***" if isinstance(k, str) and _SECRET_KEY.search(k) and v not in (None, "", False) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# --- confirmation tokens -----------------------------------------------------------------------------------------


@dataclass
class _Issued:
    tool: str
    digest: str
    expires: float


_ISSUED: dict[str, _Issued] = {}


def _digest(tool: str, args: dict[str, Any], binding: str | None) -> str:
    canonical = json.dumps(
        {"tool": tool, "args": to_jsonable_python(args), "binding": binding}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _issue(tool: str, digest: str) -> str:
    now = time.monotonic()
    for key in [k for k, v in _ISSUED.items() if v.expires < now]:
        del _ISSUED[key]
    token = "cfm_" + secrets.token_urlsafe(12)
    _ISSUED[token] = _Issued(tool=tool, digest=digest, expires=now + write_settings.confirmation_ttl_sec)
    return token


def _redeem(token: str, tool: str, digest: str) -> None:
    issued = _ISSUED.pop(token, None)
    if issued is None or issued.expires < time.monotonic():
        raise ToolError(
            "confirm_token is unknown, already used or expired. Call the tool again without confirm_token to get a "
            "fresh plan, show it to the user and wait for their yes."
        )
    if issued.tool != tool or issued.digest != digest:
        raise ToolError(
            "confirm_token was issued for a different call (other tool, other arguments, or the target object "
            "changed since the plan was made). Call the tool without confirm_token to get a new plan."
        )


def _client_supports_elicitation(ctx: Context | None) -> bool:
    if ctx is None:
        return False
    try:
        params = ctx.session.client_params
        return bool(params and params.capabilities and params.capabilities.elicitation is not None)
    except Exception:
        return False


# --- audit -------------------------------------------------------------------------------------------------------


def audit(event: dict[str, Any]) -> None:
    path = write_settings.audit_log
    if not path:
        return
    record = {"ts": datetime.now(UTC).isoformat(), **event}
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # auditing must never break the action itself


def _client_id(ctx: Context | None) -> str | None:
    try:
        return ctx.client_id if ctx is not None else None
    except Exception:
        return None


def _shorten(value: Any, limit: int = 2000) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= limit else text[:limit] + " ..."


# --- the executor ------------------------------------------------------------------------------------------------


def needs_confirmation(level: Level) -> bool:
    if write_settings.confirmation_mode == "none":
        return False
    return level is Level.DESTRUCTIVE or (level is Level.WRITE and write_settings.confirm_writes)


def tool_args(local_vars: dict[str, Any]) -> dict[str, Any]:
    """A tool's own arguments, for binding a confirmation token: `locals()` minus context and the token itself."""
    return {k: v for k, v in local_vars.items() if k not in ("ctx", "confirm_token") and not k.startswith("_")}


async def execute(
    ctx: Context | None,
    plan: Plan,
    *,
    args: dict[str, Any],
    confirm_token: str | None = None,
    binding: str | None = None,
) -> dict[str, Any]:
    """Confirm (if required), then send `plan.requests` in order and return what VBR answered.

    `binding` ties a confirmation token to state beyond the arguments, e.g. a hash of the object being edited, so a
    token cannot be redeemed after someone else changed that object.
    """
    from vbr_write_mcp.server import vbr_client  # late import: server imports the tool modules

    if _exceeds(plan.level):
        raise ToolError(
            f"This action is '{plan.level.value}' but the server runs with VBRW_ACCESS_LEVEL="
            f"{write_settings.access_level}. Ask an administrator to raise it if this action is intended."
        )

    digest = _digest(plan.tool, args, binding)
    base_event = {"tool": plan.tool, "level": plan.level.value, "client": _client_id(ctx), "summary": plan.summary}

    if needs_confirmation(plan.level):
        if confirm_token:
            _redeem(confirm_token, plan.tool, digest)
            base_event["confirmation"] = "token"
        else:
            mode = write_settings.confirmation_mode
            elicited = False
            if mode == "elicit" or (mode == "auto" and _client_supports_elicitation(ctx)):
                if _is_modern(ctx):
                    # 2026-07-28+ protocol: no server-initiated requests; ask through the multi-round-trip guard.
                    answer = _guard_answer(ctx, digest)
                    if answer is None:
                        audit({**base_event, "status": "confirmation_requested", "plan": plan.describe()})
                        # The tool returns this instead of its dict: FastMCP turns it into the protocol's input-required result.
                        return _guard_request(plan, digest)  # ty: ignore[invalid-explicit-return]
                else:
                    answer = await _elicit(ctx, plan)
                if answer is None and mode == "elicit":
                    raise ToolError(
                        "VBRW_CONFIRMATION_MODE=elicit but this MCP client does not support elicitation: use "
                        "VBRW_CONFIRMATION_MODE=auto or token."
                    )
                if answer is False:
                    audit({**base_event, "status": "declined_by_user", "plan": plan.describe()})
                    return {"status": "cancelled", "message": "The user declined the action. Nothing was sent to VBR."}
                elicited = answer is True
                if elicited:
                    base_event["confirmation"] = "elicitation"
            if not elicited:
                token = _issue(plan.tool, digest)
                audit({**base_event, "status": "confirmation_required", "plan": plan.describe()})
                return {
                    "status": "confirmation_required",
                    "plan": plan.describe(),
                    "confirm_token": token,
                    "expires_in_sec": write_settings.confirmation_ttl_sec,
                    "next_step": (
                        "Nothing has been sent yet. Show this plan (target, what it does, impact) to the user and ask "
                        "for an explicit yes. Only after the yes, call the same tool again with exactly the same "
                        "arguments plus confirm_token. Never confirm on the user's behalf."
                    ),
                }

    if write_settings.dry_run:
        audit({**base_event, "status": "dry_run", "plan": plan.describe()})
        return {"status": "dry_run", "message": "VBRW_DRY_RUN is on: nothing was sent.", "plan": plan.describe()}

    results = []
    for i, req in enumerate(plan.requests):
        try:
            result = await vbr_client.request(
                req.method, req.path, params=req.params, json=req.body, timeout=req.timeout
            )
        except ToolError as e:
            audit({**base_event, "status": "failed", "request": req.describe(), "error": str(e), "completed": i})
            done = f" {i} of {len(plan.requests)} request(s) had already succeeded." if i else ""
            raise ToolError(f"{e}{done}") from e
        results.append(result)
    audit(
        {
            **base_event,
            "status": "executed",
            "requests": [r.describe() for r in plan.requests],
            "result": _shorten(redact(to_jsonable_python(results))),
        }
    )
    out: dict[str, Any] = {
        "status": "submitted",
        "summary": plan.summary,
        "result": results[0] if len(results) == 1 else results,
        "note": (
            "VBR runs most actions asynchronously: report this as started, not finished. If the result carries a "
            "session id, follow it with get_session_result."
        ),
    }
    return out


_ORDER = {"read": 0, "write": 1, "destructive": 2}


def _exceeds(level: Level) -> bool:
    return _ORDER[level.value] > _ORDER[write_settings.access_level]


_CONFIRM_KEY = "vbrw_confirm"
_CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {
            "type": "boolean",
            "title": "Run it",
            "description": "Tick to run the action on the backup server, leave empty to cancel.",
        }
    },
    "required": ["value"],
}


def _confirm_message(plan: Plan) -> str:
    return "Veeam Backup & Replication - confirm this action:\n\n" + plan.render()


def _is_modern(ctx: Context | None) -> bool:
    try:
        return bool(ctx is not None and ctx._is_modern_protocol())
    except Exception:
        return False


def _guard_request(plan: Plan, digest: str) -> mcp_types.InputRequiredResult:
    params = mcp_types.ElicitRequestFormParams(message=_confirm_message(plan), requested_schema=_CONFIRM_SCHEMA)
    return mcp_types.InputRequiredResult(
        input_requests={_CONFIRM_KEY: mcp_types.ElicitRequest(params=params)},
        request_state=digest,
    )


def _guard_answer(ctx: Context | None, digest: str) -> bool | None:
    """The user's answer from a guard round-trip; None on the first round (nothing asked yet)."""
    try:
        responses = ctx.input_responses if ctx is not None else None
        state = ctx.request_state if ctx is not None else None
    except Exception:
        return None
    if not responses or _CONFIRM_KEY not in responses or state != digest:
        return None
    result = responses[_CONFIRM_KEY]
    content = getattr(result, "content", None) or {}
    return getattr(result, "action", None) == "accept" and bool(content.get("value"))


async def _elicit(ctx: Context | None, plan: Plan) -> bool | None:
    """True/False for the user's answer; None when elicitation is unavailable."""
    if ctx is None:
        return None
    try:
        result = await ctx.elicit(
            message=_confirm_message(plan),
            response_type=bool,
            response_title="Run it",
            response_description="Tick to run the action on the backup server, leave empty to cancel.",
        )
    except Exception:
        return None
    if getattr(result, "action", None) == "accept":
        return bool(getattr(result, "data", False))
    return False


def object_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(to_jsonable_python(obj), sort_keys=True, default=str).encode()).hexdigest()[:16]


_SUMMARY_KEYS = (
    "id",
    "name",
    "type",
    "platform",
    "description",
    "status",
    "isDisabled",
    "isEnabled",
    "path",
    "hostName",
    "jobName",
    "vmName",
    "state",
    "creationTime",
    "repositoryId",
    "repositoryName",
    "platformName",
    "objectId",
)


def summarize(obj: Any) -> Any:
    """The identifying fields of a VBR object, for a compact plan preview."""
    if isinstance(obj, dict):
        picked = {k: obj[k] for k in _SUMMARY_KEYS if k in obj}
        return picked or {k: obj[k] for k in list(obj)[:8]}
    if isinstance(obj, list):
        return [summarize(o) for o in obj[:20]]
    return obj


async def try_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET `path` for a plan preview; on failure return the reason instead of failing the whole call."""
    from vbr_write_mcp.server import vbr_client

    try:
        return await vbr_client.get(path, params=params)
    except ToolError as e:
        return {"previewUnavailable": str(e)}


async def run(
    ctx: Context | None,
    tool: str,
    level: Level,
    summary: str,
    requests: Request | list[Request],
    *,
    args: dict[str, Any],
    confirm_token: str | None,
    impact: list[str] | None = None,
    preview: Any = None,
    binding: str | None = None,
) -> dict[str, Any]:
    """Shorthand for building a `Plan` and passing it to `execute`."""
    plan = Plan(
        tool=tool,
        level=level,
        summary=summary,
        requests=requests if isinstance(requests, list) else [requests],
        impact=[i for i in (impact or []) if i],
        preview=preview,
    )
    return await execute(ctx, plan, args=args, confirm_token=confirm_token, binding=binding)


def name_of(obj: Any, fallback: Any) -> str:
    if isinstance(obj, dict):
        for key in ("name", "vmName", "jobName", "fileName", "username", "hint"):
            if obj.get(key):
                return str(obj[key])
    return str(fallback)
