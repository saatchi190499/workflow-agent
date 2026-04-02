# Adding Clients And Routes

## Runtime clients (available inside workflow code)

Main files:
- `workflow_shared.py` - shared client/helper implementation (example: `TeamsClient.send_alert`)
- `main.py` - runtime object wiring (`_build_base_context`) and variable visibility (`HIDDEN_TIP_NAMES`)

When adding a new client object:
1. Implement/import the client in `workflow_shared.py` (or another module).
2. Create the instance in `main.py`.
3. Expose it in `_build_base_context()` so workflow code can use it.
4. If it should not appear in variable tips, add its name to `HIDDEN_TIP_NAMES`.

## API routes

Current structure is single-file routing:
- All FastAPI endpoints are declared in `main.py`.

Add a new endpoint directly in `main.py` with `@app.get(...)`, `@app.post(...)`, etc.

## Remote import clients (`/api/module/...` from main backend)

`main.py` currently imports remote modules through `RemoteModuleFinder` and `RemoteModuleLoader`.

If adding a new remote client package exposed by backend integration:
1. Add the package prefix to the finder condition in `RemoteModuleFinder.find_spec(...)`.
2. Import the module/client and expose it in `_build_base_context()`.

## Notes

- Keep only `send_alert(...)` method name for Teams client usage.
- Keep this repo's `workflow_shared.py` aligned with `prodcast-worker/worker/workflow_shared.py`.
## Canonical shared source

Preferred source for shared runtime helpers is now:
- `C:/Users/Administrator/Desktop/ProdCast2.0/backend/mainapp/apiapp/utils/workflow_runtime_shared.py`

`main.py` tries remote import from this module first, then falls back to local `workflow_shared.py`.
## Deployment Topology

`workflow-agent` is deployed on a different server from `ProdCast2.0` and `prodcast-worker`.
Remote shared imports require connectivity to `ProdCast2.0 /api/module/...`; otherwise local fallback is used.
Security note:
- backend `/api/module/<path>` now requires `X-API-Key`
- set `WORKFLOW_AGENT_API_KEY` to match backend `INTEGRATION_MODULE_API_KEY` (or `WORKFLOW_MODULE_API_KEY`)
