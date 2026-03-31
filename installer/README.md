# MSI Packaging

1. Build MSI:

```powershell
cd C:\path\to\workflow-agent
powershell -ExecutionPolicy Bypass -File .\installer\build_msi.ps1 `
  -Version 1.0.0 `
  -MainServerUrl "https://your-prodcast-host/api" `
  -DisableSslVerify
```

2. Output MSI:

`dist\msi\WorkflowAgent-<version>.msi`

3. Install on target server:

```powershell
msiexec /i WorkflowAgent-1.0.0.msi /qn
```

4. Service name:

`WorkflowAgentService`

5. Notes:

- WiX v4 is required on build machine (`dotnet tool install --global wix`).
- `build_msi.ps1` downloads WinSW wrapper automatically.
- Runner binary is built first using `build_release.ps1`.
