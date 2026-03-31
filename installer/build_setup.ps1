param(
    [ValidateSet("PyInstaller", "Nuitka")]
    [string]$BuildTool = "PyInstaller",
    [switch]$SkipExeBuild,
    [string]$ISCCPath = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location -LiteralPath $repoRoot

$buildScript = Join-Path $repoRoot "scripts\build\build_release.ps1"
if (-not (Test-Path -LiteralPath $buildScript)) {
    throw "Missing build script: $buildScript"
}

if (-not $SkipExeBuild) {
    & powershell -ExecutionPolicy Bypass -File $buildScript -BuildTool $BuildTool
}

$runner = Join-Path $repoRoot "dist\release\WorkflowAgentRunner.exe"
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Missing runner EXE: $runner"
}

if (-not $ISCCPath) {
    $candidates = @(
        "C:\Users\Administrator\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )

    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) {
            $ISCCPath = $c
            break
        }
    }

    if (-not $ISCCPath) {
        $cmd = Get-Command iscc -ErrorAction SilentlyContinue
        if ($cmd) { $ISCCPath = $cmd.Source }
    }
}

if (-not $ISCCPath -or -not (Test-Path -LiteralPath $ISCCPath)) {
    throw "ISCC.exe not found. Install Inno Setup or pass -ISCCPath."
}

$iss = Join-Path $repoRoot "installer\workflow-agent.iss"
if (-not (Test-Path -LiteralPath $iss)) {
    throw "Missing installer script: $iss"
}

New-Item -ItemType Directory -Path (Join-Path $repoRoot "dist\installer") -Force | Out-Null

Write-Host "Building setup.exe with ISCC: $ISCCPath"
& $ISCCPath "/DSourceRoot=$repoRoot" $iss

$latest = Get-ChildItem -Path (Join-Path $repoRoot "dist\installer") -Filter "WorkflowAgentSetup-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $latest) {
    throw "Installer build completed but no setup EXE was found in dist\\installer"
}

Write-Host "Installer created: $($latest.FullName)"
