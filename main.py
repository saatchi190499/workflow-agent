"""
Local Workflow Agent (FastAPI)

Runs workflow cells locally, but fetches workflow inputs from the Django server
based on the workflow's saved `io_config`.
"""

import io
import os
import sys
import types
import json
from datetime import datetime, timezone
from pathlib import Path
import importlib.abc
import importlib.util
from contextlib import redirect_stderr, redirect_stdout, contextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

def _apply_request_auth(request: Request):
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        if token:
            internal.auth_token = token
    refresh = request.headers.get("x-refresh-token") or request.headers.get("X-Refresh-Token")
    if refresh:
        internal.refresh_token = refresh

# ==============================================================
# 🔹 Remote Import Setup (petex_client + pi_client)
# ==============================================================

MAIN_SERVER_URL = os.getenv("WORKFLOW_AGENT_MAIN_SERVER_URL", "http://btlweb:8000/api")
MAIN_SERVER_MODULE_URL = f"{MAIN_SERVER_URL}/module"  # Django integration get_module endpoint
MAIN_SERVER_WORKFLOW_INPUTS_URL = f"{MAIN_SERVER_URL}/workflow_inputs"
MAIN_SERVER_TOKEN_URL = f"{MAIN_SERVER_URL}/me/"
MAIN_SERVER_REFRESH_URL = f"{MAIN_SERVER_URL}/token/refresh/"
API_KEY = os.getenv("WORKFLOW_AGENT_API_KEY", "supersecret")
AUTH_TOKEN = os.getenv("WORKFLOW_AGENT_AUTH_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzY5MDY1Mzg5LCJpYXQiOjE3Njg5OTIxNzMsImp0aSI6Ijk4ZTQwOWQ5MjhhNjQ2MDFiNzMzZjM4MmZhYWJiNzliIiwidXNlcl9pZCI6MX0.zrG9NXFCiQMpu9tD5Vlsh9jgdKEocy0j3W7j8sPwVMU")
USERNAME = os.getenv("WORKFLOW_AGENT_USERNAME", "")
PASSWORD = os.getenv("WORKFLOW_AGENT_PASSWORD", "")
REFRESH_TOKEN = os.getenv("WORKFLOW_AGENT_REFRESH_TOKEN", "")
OUTPUT_MODE = os.getenv("WORKFLOW_AGENT_OUTPUT_MODE", "local")

DISABLE_REMOTE_IMPORTS = os.getenv("WORKFLOW_AGENT_DISABLE_REMOTE_IMPORTS", "").lower() in (
    "1",
    "true",
    "yes",
)
DISABLE_PETEX = os.getenv("WORKFLOW_AGENT_DISABLE_PETEX", "").lower() in (
    "1",
    "true",
    "yes",
)


class RemoteModuleLoader(importlib.abc.SourceLoader):
    def __init__(self, fullname):
        self.fullname = fullname

    def get_data(self, path):
        try:
            import requests
        except ImportError as e:
            raise ImportError("requests is required for remote imports") from e

        module_path = path.replace(".", "/")
        url = f"{MAIN_SERVER_MODULE_URL}/{module_path}"
        headers = {"X-API-Key": API_KEY}
        resp = requests.get(url, headers=headers, timeout=30)

        if resp.status_code == 404 and "/" not in module_path.split("/")[-1]:
            url = f"{MAIN_SERVER_MODULE_URL}/{module_path}/__init__.py"
            resp = requests.get(url, headers=headers, timeout=30)

        if resp.status_code != 200:
            raise ImportError(f"Failed to fetch: {url} ({resp.status_code})")

        return resp.text.encode("utf-8")

    def get_filename(self, fullname):
        return fullname

    def is_package(self, fullname):
        return fullname in ("petex_client", "pi_client", "apiapp", "apiapp.domains", "apiapp.domains.data")


class RemoteModuleFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("petex_client", "pi_client", "apiapp")):
            loader = RemoteModuleLoader(fullname)
            return importlib.util.spec_from_loader(fullname, loader)
        return None


if not DISABLE_REMOTE_IMPORTS:
    sys.meta_path.insert(0, RemoteModuleFinder())

# ==============================================================
# 🔹 Import Petex & PI limited functions
# ==============================================================

PETEX_IMPORT_ERROR = None
PI_IMPORT_ERROR = None


def _unavailable(name: str, err):
    def _fn(*_args, **_kwargs):
        details = f": {err}" if err else ""
        raise RuntimeError(f"{name} is unavailable{details}")

    return _fn


try:
    if DISABLE_PETEX:
        raise ImportError("disabled via WORKFLOW_AGENT_DISABLE_PETEX=1")
    import petex_client.gap as gap
    import petex_client.gap_tools as gap_tools
    import petex_client.resolve as resolve
    from petex_client.server import PetexServer
except Exception as e:
    PETEX_IMPORT_ERROR = e
    gap = None
    gap_tools = None
    resolve = None
    PetexServer = None

try:
    import pi_client  # load root

    pi_value = pi_client.value
    pi_series = pi_client.series
except Exception as e:
    PI_IMPORT_ERROR = e
    pi_value = _unavailable("pi.value", e)
    pi_series = _unavailable("pi.series", e)

# ==============================================================
# 🔹 FastAPI Setup
# ==============================================================

app = FastAPI(title="Workflow Agent (Petex + PI)", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================
# 🔹 Global Context
# ==============================================================


@contextmanager
def _petex_server(enabled: bool):
    if not enabled:
        yield None
        return
    if PetexServer is None:
        details = f": {PETEX_IMPORT_ERROR}" if PETEX_IMPORT_ERROR else ""
        raise RuntimeError(f"Petex is unavailable{details}")
    with PetexServer() as srv:
        yield srv


def _fetch_workflow_inputs(*, workflow_component_id: int):
    try:
        import requests
    except ImportError as e:
        raise ImportError("requests is required for workflow_load_inputs()") from e

    url = f"{MAIN_SERVER_WORKFLOW_INPUTS_URL}/{int(workflow_component_id)}/"
    headers = {"X-API-Key": API_KEY}
    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch workflow inputs ({resp.status_code}): {resp.text}")
    return resp.json()


class InternalClient:
    def __init__(self, base_url: str, api_key: str | None = None, auth_token: str | None = None, username: str | None = None, password: str | None = None, refresh_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.auth_token = auth_token or ""
        self.username = username or ""
        self.password = password or ""
        self.refresh_token = refresh_token or ""

    def _refresh_token(self):
        if not self.refresh_token:
            return False
        try:
            import requests
        except ImportError as e:
            raise ImportError("requests is required for token refresh") from e
        resp = requests.post(MAIN_SERVER_REFRESH_URL, json={"refresh": self.refresh_token}, timeout=30)
        if resp.status_code != 200:
            return False
        data = resp.json()
        token = data.get("access") or data.get("token")
        if not token:
            return False
        self.auth_token = token
        return True

    def _ensure_token(self):
        if self.auth_token:
            return
        if self.refresh_token:
            if self._refresh_token():
                return
        if not self.username or not self.password:
            return
        try:
            import requests
        except ImportError as e:
            raise ImportError("requests is required for InternalClient auth") from e
        resp = requests.post(MAIN_SERVER_TOKEN_URL, json={"username": self.username, "password": self.password}, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Auth token request failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        token = data.get("access") or data.get("token")
        if not token:
            raise RuntimeError("Auth token missing in response")
        self.auth_token = token

    def _headers(self):
        self._ensure_token()
        if not self.auth_token:
            raise RuntimeError('Missing auth token. Set WORKFLOW_AGENT_AUTH_TOKEN or WORKFLOW_AGENT_USERNAME/WORKFLOW_AGENT_PASSWORD.')
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _request(self, path: str, params: dict | None = None):
        try:
            import requests
        except ImportError as e:
            raise ImportError("requests is required for InternalClient") from e
        url = f"{self.base_url}{path}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=60)
        if resp.status_code == 401 and self._refresh_token():
            resp = requests.get(url, headers=self._headers(), params=params, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"Internal API failed ({resp.status_code}): {resp.text}")
        return resp.json()

    def _parse_dt(self, value):
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            s = str(value)
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(s)
            except Exception:
                return None
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _components(self):
        return self._request("/data-sources/Internal/components/")

    def _metadata(self):
        return self._request("/object-metadata/")

    def _build_meta_maps(self, meta):
        type_map = {t.get("id"): t.get("name") for t in meta.get("types", [])}
        instance_map = {}
        for _tname, insts in (meta.get("instances") or {}).items():
            for inst in insts:
                instance_map[inst.get("id")] = inst.get("name")
        prop_map = {}
        for _tname, props in (meta.get("properties") or {}).items():
            for prop in props:
                prop_map[prop.get("id")] = prop.get("name")
        return type_map, instance_map, prop_map

    def _resolve_component_ids(self, components):
        comps = self._components()
        by_id = {c.get("id"): c for c in comps}
        by_name = {str(c.get("name", "")).lower(): c for c in comps}
        if not components:
            ids = [c.get("id") for c in comps]
            return [i for i in ids if i], by_id
        ids = []
        for item in components:
            if item is None:
                continue
            if isinstance(item, dict):
                if item.get("id") is not None:
                    ids.append(int(item["id"]))
                elif item.get("component_id") is not None:
                    ids.append(int(item["component_id"]))
                elif item.get("name"):
                    comp = by_name.get(str(item["name"]).lower())
                    if comp:
                        ids.append(comp.get("id"))
                continue
            if hasattr(item, "pk"):
                ids.append(int(item.pk))
                continue
            if isinstance(item, int):
                ids.append(item)
                continue
            if isinstance(item, str):
                if item.isdigit():
                    ids.append(int(item))
                else:
                    comp = by_name.get(item.lower())
                    if comp:
                        ids.append(comp.get("id"))
                continue
        ids = [i for i in ids if i in by_id]
        return sorted(set(ids)), by_id

    def _resolve_type_ids(self, object_type, meta):
        if object_type is None:
            return set()
        types = meta.get("types", [])
        by_name = {str(t.get("name", "")).lower(): t.get("id") for t in types}
        items = object_type if isinstance(object_type, (list, tuple, set)) else [object_type]
        ids = set()
        for item in items:
            if item is None:
                continue
            if isinstance(item, dict):
                if item.get("id") is not None:
                    ids.add(int(item["id"]))
                elif item.get("object_type_id") is not None:
                    ids.add(int(item["object_type_id"]))
                elif item.get("name"):
                    val = by_name.get(str(item["name"]).lower())
                    if val:
                        ids.add(int(val))
                continue
            if hasattr(item, "pk"):
                ids.add(int(item.pk))
                continue
            if isinstance(item, int):
                ids.add(item)
                continue
            if isinstance(item, str):
                if item.isdigit():
                    ids.add(int(item))
                else:
                    val = by_name.get(item.lower())
                    if val:
                        ids.add(int(val))
                continue
        return ids

    def _resolve_instance_ids(self, instances, meta):
        if not instances:
            return set()
        instance_map = meta.get("instances", {})
        by_name = {}
        for _tname, insts in instance_map.items():
            for inst in insts:
                by_name[str(inst.get("name", "")).lower()] = inst.get("id")
        ids = set()
        for item in instances:
            if item is None:
                continue
            if isinstance(item, dict):
                if item.get("id") is not None:
                    ids.add(int(item["id"]))
                elif item.get("object_instance_id") is not None:
                    ids.add(int(item["object_instance_id"]))
                elif item.get("name"):
                    val = by_name.get(str(item["name"]).lower())
                    if val:
                        ids.add(int(val))
                continue
            if hasattr(item, "pk"):
                ids.add(int(item.pk))
                continue
            if isinstance(item, int):
                ids.add(item)
                continue
            if isinstance(item, str):
                if item.isdigit():
                    ids.add(int(item))
                else:
                    val = by_name.get(item.lower())
                    if val:
                        ids.add(int(val))
                continue
        return ids

    def _resolve_property_ids(self, properties, meta):
        if not properties:
            return set()
        props_map = meta.get("properties", {})
        by_name = {}
        for _tname, props in props_map.items():
            for prop in props:
                by_name[str(prop.get("name", "")).lower()] = prop.get("id")
        ids = set()
        for item in properties:
            if item is None:
                continue
            if isinstance(item, dict):
                if item.get("id") is not None:
                    ids.add(int(item["id"]))
                elif item.get("object_type_property_id") is not None:
                    ids.add(int(item["object_type_property_id"]))
                elif item.get("name"):
                    val = by_name.get(str(item["name"]).lower())
                    if val:
                        ids.add(int(val))
                continue
            if hasattr(item, "pk"):
                ids.add(int(item.pk))
                continue
            if isinstance(item, int):
                ids.add(item)
                continue
            if isinstance(item, str):
                if item.isdigit():
                    ids.add(int(item))
                else:
                    val = by_name.get(item.lower())
                    if val:
                        ids.add(int(val))
                continue
        return ids

    def get_records(self, components=None, object_type=None, instances=None, properties=None):
        comp_ids, comp_by_id = self._resolve_component_ids(components)
        meta = self._metadata()
        type_ids = self._resolve_type_ids(object_type, meta)
        instance_ids = self._resolve_instance_ids(instances, meta)
        property_ids = self._resolve_property_ids(properties, meta)
        out = []
        for comp_id in comp_ids:
            records = self._request(f"/components/internal/{int(comp_id)}")
            for rec in records:
                if type_ids and rec.get("object_type") not in type_ids:
                    continue
                if instance_ids and rec.get("object_instance") not in instance_ids:
                    continue
                if property_ids and rec.get("object_type_property") not in property_ids:
                    continue
                rec["component_id"] = rec.get("component") or comp_id
                comp = comp_by_id.get(comp_id) or {}
                if comp:
                    rec["component_name"] = comp.get("name", "")
                out.append(rec)
        return out

    def get_history(self, components=None, object_type=None, instances=None, properties=None, start=None, end=None):
        records = self.get_records(components=components, object_type=object_type, instances=instances, properties=properties)
        meta = self._metadata()
        type_map, instance_map, prop_map = self._build_meta_maps(meta)
        start_dt = self._parse_dt(start)
        end_dt = self._parse_dt(end)
        out = []
        for rec in records:
            comp_id = rec.get("component") or rec.get("component_id")
            row_id = rec.get("data_set_id") or rec.get("id")
            if not comp_id or not row_id:
                continue
            history = self._request(f"/components/{int(comp_id)}/row/{int(row_id)}/history/")
            for item in history:
                t = item.get("time")
                dt = self._parse_dt(t)
                if start_dt and dt and dt < start_dt:
                    continue
                if end_dt and dt and dt > end_dt:
                    continue
                type_id = rec.get("object_type")
                instance_id = rec.get("object_instance")
                prop_id = rec.get("object_type_property")
                item["component_id"] = comp_id
                item["main_record_id"] = row_id
                item["object_type_name"] = type_map.get(type_id, "")
                item["object_instance_name"] = instance_map.get(instance_id, "")
                item["object_type_property_name"] = prop_map.get(prop_id, "")
                out.append(item)
        return out

internal = InternalClient(MAIN_SERVER_URL, api_key=API_KEY, auth_token=AUTH_TOKEN, username=USERNAME, password=PASSWORD, refresh_token=REFRESH_TOKEN)

GLOBAL_CONTEXT = {
    "gap": gap,
    "gap_tools": gap_tools,
    "resolve": resolve,
    "PetexServer": PetexServer,
    "pi": types.SimpleNamespace(
        value=pi_value,
        series=pi_series,
    ),
    "internal": internal,
}

# ==============================================================
# 🔹 Local workflow output persistence (testing only)
# ==============================================================

WORKFLOW_AGENT_OUTPUT_DIR = Path(os.getenv("WORKFLOW_AGENT_OUTPUT_DIR", "./workflow_outputs")).resolve()


def _workflow_output_path(*, workflow_component_id: int) -> Path:
    return WORKFLOW_AGENT_OUTPUT_DIR / f"workflow_{int(workflow_component_id)}.jsonl"


def _save_workflow_output_local(*, workflow_component_id: int, records, mode: str = "append"):
    if mode not in ("append", "replace"):
        raise ValueError("mode must be 'append' or 'replace'")

    WORKFLOW_AGENT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _workflow_output_path(workflow_component_id=int(workflow_component_id))

    items = records if isinstance(records, list) else [records]
    text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in (items or []))

    if mode == "replace":
        path.write_text(text, encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as f:
            f.write(text)

    GLOBAL_CONTEXT["workflow_last_output_path"] = str(path)
    GLOBAL_CONTEXT["workflow_last_output_count"] = len(items or [])
    return {"status": "saved_local", "path": str(path), "count": len(items or [])}


# ==============================================================
# 🔹 Error Handler
# ==============================================================


@app.exception_handler(Exception)
async def all_exceptions_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": str(exc)})


# ==============================================================
# 🔹 Execute Code (Single Cell)
# ==============================================================


@app.post("/run_cell/")
async def run_cell(request: Request):
    data = await request.json()
    code = data.get("code", "")
    use_petex = bool(data.get("use_petex", False))
    workflow_component_id = data.get("workflow_component_id")
    _apply_request_auth(request)

    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()

    try:
        with _petex_server(use_petex) as srv:
            if srv is not None:
                GLOBAL_CONTEXT["srv"] = srv

            if workflow_component_id:
                GLOBAL_CONTEXT["workflow_component_id"] = int(workflow_component_id)
                GLOBAL_CONTEXT["workflow_load_inputs"] = lambda: _fetch_workflow_inputs(
                    workflow_component_id=int(workflow_component_id)
                )
                GLOBAL_CONTEXT["workflow_save_output"] = lambda records, mode="append", save_to=None, component_id=None: (
                    _save_workflow_output_db(
                        workflow_component_id=int(workflow_component_id), records=records, component_id=component_id
                    )
                    if (save_to == "db" or OUTPUT_MODE == "db")
                    else _save_workflow_output_local(
                        workflow_component_id=int(workflow_component_id), records=records, mode=mode
                    )
                )

            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(code, GLOBAL_CONTEXT)
    except Exception as e:
        stderr_buf.write(f"{type(e).__name__}: {e}\n")
    finally:
        GLOBAL_CONTEXT.pop("srv", None)

    vars_snapshot = {
        k: {"type": type(v).__name__, "preview": str(v)[:60]}
        for k, v in GLOBAL_CONTEXT.items()
        if not k.startswith("__")
        and not callable(v)
        and not isinstance(v, type)
        and k not in {"gap", "resolve", "PetexServer", "srv"}
    }

    return JSONResponse(
        {
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
            "variables": vars_snapshot,
        }
    )


@app.post("/run_all/")
async def run_all(request: Request):
    data = await request.json()
    cells = data.get("cells", [])
    use_petex = bool(data.get("use_petex", False))
    workflow_component_id = data.get("workflow_component_id")
    _apply_request_auth(request)

    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()

    try:
        with _petex_server(use_petex) as srv:
            if srv is not None:
                GLOBAL_CONTEXT["srv"] = srv

            if workflow_component_id:
                GLOBAL_CONTEXT["workflow_component_id"] = int(workflow_component_id)
                GLOBAL_CONTEXT["workflow_load_inputs"] = lambda: _fetch_workflow_inputs(
                    workflow_component_id=int(workflow_component_id)
                )
                GLOBAL_CONTEXT["workflow_save_output"] = lambda records, mode="append", save_to=None, component_id=None: (
                    _save_workflow_output_db(
                        workflow_component_id=int(workflow_component_id), records=records, component_id=component_id
                    )
                    if (save_to == "db" or OUTPUT_MODE == "db")
                    else _save_workflow_output_local(
                        workflow_component_id=int(workflow_component_id), records=records, mode=mode
                    )
                )

            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                for code in cells:
                    exec(code, GLOBAL_CONTEXT)
    except Exception as e:
        stderr_buf.write(f"{type(e).__name__}: {e}\n")
    finally:
        GLOBAL_CONTEXT.pop("srv", None)

    vars_snapshot = {
        k: {"type": type(v).__name__, "preview": str(v)[:60]}
        for k, v in GLOBAL_CONTEXT.items()
        if not k.startswith("__")
        and not callable(v)
        and not isinstance(v, type)
        and k not in {"gap", "resolve", "PetexServer", "srv"}
    }

    return JSONResponse(
        {
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
            "variables": vars_snapshot,
        }
    )


# ==============================================================
# 🔹 Variable Management (for NotebookEditor UI)
# ==============================================================


@app.get("/variables/")
async def list_variables():
    reserved = {"gap", "gap_tools", "resolve", "PetexServer", "srv"}
    result = {}
    for k, v in GLOBAL_CONTEXT.items():
        if k.startswith("__") or k in reserved or callable(v) or isinstance(v, type):
            continue
        try:
            preview = str(v)
            if len(preview) > 80:
                preview = preview[:77] + "..."
            result[k] = {"type": type(v).__name__, "preview": preview}
        except Exception:
            result[k] = {"type": "unknown", "preview": ""}
    return JSONResponse(result)


@app.post("/reset_context/")
async def reset_context():
    GLOBAL_CONTEXT.clear()
    GLOBAL_CONTEXT.update(
        {
            "gap": gap,
            "gap_tools": gap_tools,
            "resolve": resolve,
            "PetexServer": PetexServer,
            "pi": types.SimpleNamespace(
                value=pi_value,
                series=pi_series,
            ),
            "internal": internal,
        }
    )
    return JSONResponse({"status": "reset"})


@app.post("/delete_var/")
async def delete_var(request: Request):
    data = await request.json()
    name = data.get("name")
    if name and name in GLOBAL_CONTEXT:
        del GLOBAL_CONTEXT[name]
    return JSONResponse({"status": "ok", "deleted": name})


@app.post("/set_var/")
async def set_var(request: Request):
    data = await request.json()
    name = data.get("name")
    value = data.get("value")
    vtype = data.get("type", "str")
    try:
        if vtype == "int":
            value = int(value)
        elif vtype == "float":
            value = float(value)
        elif vtype == "bool":
            value = str(value).lower() in ("1", "true", "yes")
        else:
            value = str(value)
        GLOBAL_CONTEXT[name] = value
        return JSONResponse({"status": "ok", "name": name, "value": value})
    except Exception as e:
        return JSONResponse({"status": "error", "msg": str(e)}, status_code=400)


# ==============================================================
# 🔹 Local outputs (optional convenience endpoints)
# ==============================================================


@app.get("/workflow_outputs/{workflow_component_id}/")
async def get_workflow_outputs(workflow_component_id: int):
    path = _workflow_output_path(workflow_component_id=int(workflow_component_id))
    if not path.exists():
        return JSONResponse({"records": [], "path": str(path)})

    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue

    return JSONResponse({"records": records, "path": str(path)})
