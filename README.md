# Workflow Agent (EXE Deployment)

## Dependency Files

- `requirements-worker.txt`: runtime dependencies for worker/agent execution.
- `requirements-build.txt`: build-only dependencies for EXE packaging.
- `requirements.txt`: points to `requirements-worker.txt` for compatibility.

## 1) Build EXE

```powershell
cd C:\Users\Administrator\Desktop\workflow-agent
powershell -ExecutionPolicy Bypass -File .\build_release.ps1 -BuildTool PyInstaller
```

Output:

`dist\release\WorkflowAgentRunner.exe`

## 2) Copy to target server

Copy these files:

- `WorkflowAgentRunner.exe`
- `install_exe_service.ps1`
- `uninstall_exe_service.ps1`

## 3) Install service on target server

Run in Administrator PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_exe_service.ps1 `
  -RunnerExePath .\WorkflowAgentRunner.exe `
  -MainServerUrl "https://your-prodcast-host/api" `
  -DisableSslVerify
```

Verify:

```powershell
sc.exe query WorkflowAgentService
Invoke-WebRequest http://127.0.0.1:9000/variables/ -UseBasicParsing
```

## 4) Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_exe_service.ps1 -RemoveFiles
```
