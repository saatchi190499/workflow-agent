
# ==============================================================
# main.py — Agent FastAPI Server
# --------------------------------------------------------------
# Runs Petex & PI code locally, dynamically imports
# `petex_client` and `pi_client` from Django main server
# (no download), exposes only pi.value and pi.series
# ==============================================================
 
import sys
import importlib.abc
import importlib.util
import requests
import io
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import redirect_stdout, redirect_stderr
import types
 
# ==============================================================
# 🔹 Remote Import Setup (petex_client + pi_client)
# ==============================================================
 
MAIN_SERVER_URL = "http://btlweb:8000/api/module"  # your Django server endpoint
API_KEY = "supersecret"  # must match the Django view
 
class RemoteModuleLoader(importlib.abc.SourceLoader):
    """Fetch .py source from Django main server on import."""
 
    def __init__(self, fullname):
        self.fullname = fullname
 
    def get_data(self, path):
        module_path = path.replace(".", "/")
        url = f"{MAIN_SERVER_URL}/{module_path}"
        headers = {"X-API-Key": API_KEY}
 
        # 🔥 Отключаем прокси принудительно
        resp = requests.get(
            url,
            headers=headers,
            proxies={"http": None, "https": None},
        )
 
        # fallback for package root (__init__.py)
        if resp.status_code == 404 and "/" not in module_path.split("/")[-1]:
            url = f"{MAIN_SERVER_URL}/{module_path}/__init__.py"
            resp = requests.get(
                url,
                headers=headers,
                proxies={"http": None, "https": None},
            )
 
        if resp.status_code != 200:
            raise ImportError(f"❌ Failed to fetch: {url} ({resp.status_code})")
 
        return resp.text.encode("utf-8")
 
    def get_filename(self, fullname):
        return fullname
 
    def is_package(self, fullname):
        return fullname in ("petex_client", "pi_client")
 
 
class RemoteModuleFinder(importlib.abc.MetaPathFinder):
    """Intercept import requests for petex_client and pi_client."""
 
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("petex_client", "pi_client")):
            loader = RemoteModuleLoader(fullname)
            return importlib.util.spec_from_loader(fullname, loader)
        return None
 
 
sys.meta_path.insert(0, RemoteModuleFinder())
 
# ==============================================================
# 🔹 Import Petex & PI limited functions
# ==============================================================
 
import petex_client.gap as gap
import petex_client.gap_tools as gap_tools
import petex_client.resolve as resolve
from petex_client.server import PetexServer
 
import pi_client  # load root
# Expose only required functions
pi_value = pi_client.value
pi_series = pi_client.series
 
# ==============================================================
# 🔹 FastAPI Setup
# ==============================================================
 
app = FastAPI(title="Workflow Agent (Petex + PI)", version="1.0")
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
# ==============================================================
# 🔹 Global Context
# ==============================================================
 
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
 
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
 
    try:
        with PetexServer() as srv:
            GLOBAL_CONTEXT["srv"] = srv
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
 
    return JSONResponse({
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "variables": vars_snapshot,
    })
 
# ==============================================================
# 🔹 Execute Multiple Cells
# ==============================================================
 
@app.post("/run_all/")
async def run_all(request: Request):
    data = await request.json()
    cells = data.get("cells", [])
 
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
 
    try:
        with PetexServer() as srv:
            GLOBAL_CONTEXT["srv"] = srv
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
 
    return JSONResponse({
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "variables": vars_snapshot,
    })
 
# ==============================================================
# 🔹 Variable Management
# ==============================================================
 
@app.get("/variables/")
async def list_variables():
    reserved = {"gap", "resolve", "PetexServer", "srv"}
    result = {}
    for k, v in GLOBAL_CONTEXT.items():
        if (
            k.startswith("__")
            or k in reserved
            or callable(v)
            or isinstance(v, type)
        ):
            continue
        try:
            t = type(v).__name__
            preview = str(v)
            if len(preview) > 80:
                preview = preview[:77] + "..."
            result[k] = {"type": t, "preview": preview}
        except Exception:
            result[k] = {"type": "unknown", "preview": ""}
    return JSONResponse(result)
 
@app.post("/reset_context/")
async def reset_context():
    GLOBAL_CONTEXT.clear()
    GLOBAL_CONTEXT.update({
        "gap": gap,
        "gap_tools": gap_tools,
        "resolve": resolve,
        "PetexServer": PetexServer,
        "pi": types.SimpleNamespace(
            value=pi_value,
            series=pi_series,
        ),
    })
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
        return JSONResponse({"status": "error", "msg": str(e)}, status=400)