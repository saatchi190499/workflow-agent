$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    throw "Run this script in an Administrator PowerShell window."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $scriptDir

if (-not (Test-Path -LiteralPath ".\\.venv\\Scripts\\python.exe")) {
    throw ".venv python not found. Nothing to uninstall from this folder."
}

$venvPython = (Resolve-Path ".\\.venv\\Scripts\\python.exe").Path

$serviceExists = $false
try {
    $query = & sc.exe query WorkflowAgentService 2>$null | Out-String
    if ($query -match "SERVICE_NAME:\s+WorkflowAgentService") {
        $serviceExists = $true
    }
}
catch {
    $serviceExists = $false
}

if (-not $serviceExists) {
    Write-Host "WorkflowAgentService is not installed."
    exit 0
}

try {
    & $venvPython .\\windows_service.py stop
}
catch {}

& $venvPython .\\windows_service.py remove
Write-Host "WorkflowAgentService removed."
