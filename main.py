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
from pathlib import Path
import importlib.abc
import importlib.util
from contextlib import redirect_stderr, redirect_stdout, contextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ==============================================================
# 🔹 Remote Import Setup (petex_client + pi_client)
# ==============================================================

MAIN_SERVER_URL = os.getenv("WORKFLOW_AGENT_MAIN_SERVER_URL", "http://localhost:8000/api")
MAIN_SERVER_MODULE_URL = f"{MAIN_SERVER_URL}/module"  # Django integration get_module endpoint
MAIN_SERVER_WORKFLOW_INPUTS_URL = f"{MAIN_SERVER_URL}/workflow_inputs"
API_KEY = os.getenv("WORKFLOW_AGENT_API_KEY", "supersecret")

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
        return fullname in ("petex_client", "pi_client")


class RemoteModuleFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("petex_client", "pi_client")):
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


GLOBAL_CONTEXT = {
    "gap": gap,
    "gap_tools": gap_tools,
    "resolve": resolve,
    "PetexServer": PetexServer,
    "pi": types.SimpleNamespace(
        value=pi_value,
        series=pi_series,
    ),
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
                GLOBAL_CONTEXT["workflow_save_output"] = lambda records, mode="append": _save_workflow_output_local(
                    workflow_component_id=int(workflow_component_id), records=records, mode=mode
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
                GLOBAL_CONTEXT["workflow_save_output"] = lambda records, mode="append": _save_workflow_output_local(
                    workflow_component_id=int(workflow_component_id), records=records, mode=mode
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
