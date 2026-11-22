"""Regenerate src/vbr_write_mcp/data/vbr_api_<rev>.json.gz from Veeam's OpenAPI spec.

The catalog (every endpoint with its query parameters and request-body schema, plus every model) is extracted from
the `veeam-br` package, a client generated from Veeam's official VBR REST API OpenAPI specification. To refresh it
for a new API revision:

    uv venv /tmp/cat && uv pip install --python /tmp/cat veeam-br
    /tmp/cat/bin/python -I scripts/build_catalog.py > /tmp/catalog.json      # adjust VER below first
    gzip -9 -c /tmp/catalog.json > src/vbr_write_mcp/data/vbr_api_1.3-rev2.json.gz

Polymorphic request models lose their discriminator mapping in the generated client, so `x-subtypes` is rebuilt
with a field-superset heuristic plus the `_OVERRIDES` table below.
"""

import datetime
import enum
import importlib
import inspect
import json
import pkgutil
import re
import sys
import types
import typing
from uuid import UUID

VER = "v1_3_rev2"
base = importlib.import_module(f"veeam_br.{VER}.api")
Unset = importlib.import_module(f"veeam_br.{VER}.types").Unset
MODELS = vars(importlib.import_module(f"veeam_br.{VER}.models"))
File = getattr(importlib.import_module(f"veeam_br.{VER}.types"), "File", None)


def attr_docs(cls):
    doc = inspect.getdoc(cls) or ""
    out = {}
    m = re.search(r"Attributes:\n(.*)", doc, re.S)
    if not m:
        return doc.split("\n")[0] if doc else "", out
    cur = None
    for line in m.group(1).split("\n"):
        mm = re.match(r"\s*(\w+) \(([^)]*)\):\s*(.*)", line)
        if mm:
            cur = mm.group(1)
            out[cur] = mm.group(3).strip()
        elif cur and line.strip():
            out[cur] += " " + line.strip()
    head = doc[: m.start()].strip()
    return head, out


seen_models = {}


def type_schema(tp, depth):
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        opts = [a for a in args if a is not Unset and a is not type(None)]
        if len(opts) == 1:
            return type_schema(opts[0], depth)
        return {"oneOf": [type_schema(a, depth) for a in opts]}
    if origin in (list, list):
        return {"type": "array", "items": type_schema(args[0], depth) if args else {}}
    if origin in (dict,):
        return {"type": "object"}
    if tp is str:
        return {"type": "string"}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is UUID:
        return {"type": "string", "format": "uuid"}
    if tp is datetime.datetime:
        return {"type": "string", "format": "date-time"}
    if tp is datetime.date:
        return {"type": "string", "format": "date"}
    if File is not None and tp is File:
        return {"type": "string", "format": "binary"}
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return {"enum": [e.value for e in tp], "x-name": tp.__name__}
    if isinstance(tp, type) and hasattr(tp, "__attrs_attrs__"):
        return model_schema(tp, depth)
    return {"type": str(tp)}


MODEL_DEFS = {}


def model_schema(cls, depth):
    name = cls.__name__
    if name in MODEL_DEFS:
        return {"$ref": name}
    MODEL_DEFS[name] = None  # placeholder: breaks recursion
    MODEL_DEFS[name] = _model_def(cls)
    return {"$ref": name}


def _model_def(cls):
    head, docs = attr_docs(cls)
    hints = typing.get_type_hints(cls, globalns={**vars(sys.modules[cls.__module__]), **MODELS})
    props, req = {}, []
    for a in cls.__attrs_attrs__:
        if a.name == "additional_properties":
            continue
        tp = hints.get(a.name)
        opt = tp is not None and Unset in typing.get_args(tp)
        s = type_schema(tp, 1)
        if docs.get(a.name):
            s = {"description": docs[a.name], **s}
        props[_json_name(cls, a.name)] = s
        if not opt:
            req.append(_json_name(cls, a.name))
    out = {"type": "object", "properties": props}
    if req:
        out["required"] = req
    if head:
        out["description"] = head
    return out


def _json_name(cls, pyname):
    src = inspect.getsource(cls.to_dict)
    m = re.search(r'field_dict\["(\w+)"\]\s*=\s*' + re.escape(pyname) + r"\b", src)
    if m:
        return m.group(1)
    m = re.search(r'"(\w+)":\s*' + re.escape(pyname) + r"\b", src)
    return m.group(1) if m else pyname


def _query_name(src, pyname):
    m = re.search(r'params\["([^"]+)"\]\s*=\s*(?:json_)?' + re.escape(pyname) + r"\b", src)
    return m.group(1) if m else pyname


catalog = []
for mod_info in pkgutil.walk_packages(base.__path__, base.__name__ + "."):
    if mod_info.ispkg:
        continue
    mod = importlib.import_module(mod_info.name)
    if not hasattr(mod, "_get_kwargs"):
        continue
    src = inspect.getsource(mod._get_kwargs)
    method = re.search(r'"method":\s*"(\w+)"', src).group(1).upper()
    path = (
        re.search(r'"url":\s*f?"([^"]+)"', src).group(1).replace("{", "{").replace("_id}", "Id}")
        if False
        else re.search(r'"url":\s*f?"([^"]+)"', src).group(1)
    )
    doc = inspect.getdoc(mod.sync_detailed) or ""
    summary = doc.split("\n")[0].strip()
    desc = doc.split("Args:")[0].split("\n", 1)[1].strip() if "\n" in doc.split("Args:")[0] else ""
    argdocs = {}
    am = re.search(r"Args:\n(.*?)(\n\s*Raises:|\Z)", doc, re.S)
    if am:
        cur = None
        for line in am.group(1).split("\n"):
            mm = re.match(r"\s*(\w+) \(([^)]*)\):\s*(.*)", line)
            if mm:
                cur = mm.group(1)
                argdocs[cur] = mm.group(3).strip()
            elif cur and line.strip():
                argdocs[cur] += " " + line.strip()
    sig = inspect.signature(mod._get_kwargs)
    hints = typing.get_type_hints(mod._get_kwargs, globalns={**vars(mod), **MODELS})
    path_params, query, body = [], {}, None
    for p in sig.parameters.values():
        if p.name in ("x_api_version",):
            continue
        if p.name == "body":
            body = type_schema(hints["body"], 6)
            if hints["body"] is typing.Any:
                body = {"type": "object", "description": argdocs.get("body", "Free-form body (schema not published).")}
            continue
        if p.kind == p.POSITIONAL_OR_KEYWORD:
            path_params.append(p.name)
        else:
            q = type_schema(hints.get(p.name), 1)
            if argdocs.get(p.name):
                q["description"] = argdocs[p.name]
            if p.default is not inspect.Parameter.empty and not isinstance(p.default, Unset) and p.default is not None:
                q["default"] = p.default.value if isinstance(p.default, enum.Enum) else p.default
            query[_query_name(src, p.name)] = q
    catalog.append(
        {
            "method": method,
            "path": path,
            "tag": mod_info.name.split(".")[-2],
            "operation": mod_info.name.split(".")[-1],
            "summary": summary,
            "description": desc,
            "pathParams": path_params,
            "query": query,
            "body": body,
        }
    )


for _name, _cls in sorted(MODELS.items()):
    if isinstance(_cls, type) and hasattr(_cls, "__attrs_attrs__"):
        model_schema(_cls, 1)


def _words(n):
    return re.findall(r"[A-Z][a-z0-9]*|[A-Z]+(?![a-z])", n)


_props = {n: set((d or {}).get("properties", {})) for n, d in MODEL_DEFS.items()}
for base_name, base_def in MODEL_DEFS.items():
    bp = _props[base_name]
    if not base_def or not ({"type", "platform", "kind"} & bp):
        continue
    bw = _words(base_name)
    subs = []
    for n, pset in _props.items():
        if n == base_name or not bp < pset:
            continue
        w = _words(n)
        if w and bw and w[-1] == bw[-1] and (set(bw[:-1]) <= set(w) or (len(bp) >= 4 and not set(bw[:-1]) & set(w))):
            subs.append(n)
    if subs:
        base_def["x-subtypes"] = sorted(subs)

_OVERRIDES = {
    "ManagedServerSpec": [
        "ViHostSpec",
        "WindowsHostSpec",
        "LinuxHostSpec",
        "CloudDirectorHostSpec",
        "HvServerSpec",
        "HvHostClusterSpec",
        "SCVMMSpec",
        "SmbV3ClusterSpec",
        "SmbV3StandaloneHostSpec",
    ],
    "UnstructuredDataServerSpec": [
        "AmazonS3ServerSpec",
        "AzureBlobServerSpec",
        "FileServerSpec",
        "NASFilerServerSpec",
        "NFSShareServerSpec",
        "S3CompatibleServerSpec",
        "SMBShareServerSpec",
    ],
}
for _b, _subs in _OVERRIDES.items():
    if MODEL_DEFS.get(_b):
        MODEL_DEFS[_b]["x-subtypes"] = [x for x in _subs if x in MODEL_DEFS]

print(
    json.dumps(
        {"apiVersion": "1.3-rev2", "endpoints": catalog, "models": MODEL_DEFS}, default=str, separators=(",", ":")
    )
)
