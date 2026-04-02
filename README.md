# Workflow Agent (EXE + Setup Installer)

## Extension Guide

- See CLIENTS_AND_ROUTES.md for where to add new workflow runtime clients and FastAPI routes.

## Project Structure

- `scripts\build\build_release.ps1`: builds `WorkflowAgentRunner.exe`.
- `scripts\service\install_exe_service.ps1`: installs/registers service from EXE.
- `scripts\service\uninstall_exe_service.ps1`: stops/unregisters service.
- `installer\build_setup.ps1`: builds GUI installer (`setup.exe`).
- `installer\workflow-agent.iss`: Inno Setup definition.

## Dependency Files

- `requirements-worker.txt`: runtime dependencies for worker/agent execution.
- `requirements-build.txt`: build-only dependencies for EXE packaging.
- `requirements.txt`: points to `requirements-worker.txt` for compatibility.

## Build Runner EXE

```powershell
cd C:\Users\Administrator\Desktop\workflow-agent
powershell -ExecutionPolicy Bypass -File .\scripts\build\build_release.ps1 -BuildTool PyInstaller -Clean
```

Output:

`dist\release\WorkflowAgentRunner.exe`

## Build GUI Installer (setup.exe)

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build_setup.ps1
```

Output:

`dist\installer\WorkflowAgentSetup-1.0.0.exe`

## Install on target server

1. Copy and run `WorkflowAgentSetup-1.0.0.exe` as Administrator.
2. In installer wizard, set main server URL and optional flags.
3. Installer registers and starts `WorkflowAgentService`.

Verify:

```powershell
sc.exe query WorkflowAgentService
Invoke-WebRequest http://127.0.0.1:9000/variables/ -UseBasicParsing
```

## Manual Service Scripts (Optional)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\service\install_exe_service.ps1 -RunnerExePath .\dist\release\WorkflowAgentRunner.exe
powershell -ExecutionPolicy Bypass -File .\scripts\service\uninstall_exe_service.ps1 -RemoveFiles
```

## Uninstall

Use Windows Apps/Programs uninstall entry, or run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\service\uninstall_exe_service.ps1 -RemoveFiles
```

