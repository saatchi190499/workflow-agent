param(
    [string]$InstallDir = "C:\Program Files\WorkflowAgent",
    [string]$ServiceName = "WorkflowAgentService",
    [switch]$RemoveFiles
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    throw "Run this script in Administrator PowerShell."
}

$svcExe = Join-Path $InstallDir "$ServiceName.exe"

if (Test-Path -LiteralPath $svcExe) {
    try { & $svcExe stop } catch {}
    try { & $svcExe uninstall } catch {}
}
else {
    try { sc.exe stop $ServiceName | Out-Null } catch {}
    try { sc.exe delete $ServiceName | Out-Null } catch {}
}

if ($RemoveFiles -and (Test-Path -LiteralPath $InstallDir)) {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}

Write-Host "Service cleanup finished."
