param(
    [ValidateSet("PyInstaller", "Nuitka")]
    [string]$BuildTool = "PyInstaller",

    [string]$PythonPath = "python",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $scriptDir

if (-not (Test-Path -LiteralPath ".\\run.py")) {
    throw "run.py not found. Run this script from workflow-agent folder."
}

if (-not (Test-Path -LiteralPath ".\\requirements-worker.txt")) {
    throw "requirements-worker.txt not found."
}

if (-not (Test-Path -LiteralPath ".\\requirements-build.txt")) {
    throw "requirements-build.txt not found."
}

if (-not (Test-Path -LiteralPath ".\\.venv\\Scripts\\python.exe")) {
    Write-Host "Creating virtual environment..."
    & $PythonPath -m venv .venv
}

$py = (Resolve-Path ".\\.venv\\Scripts\\python.exe").Path

Write-Host "Installing worker dependencies..."
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements-worker.txt

Write-Host "Installing build dependencies..."
& $py -m pip install -r requirements-build.txt

if ($Clean) {
    Remove-Item -Recurse -Force .\\build -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force .\\dist\\release -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force .\\dist\\WorkflowAgentRunner.dist -ErrorAction SilentlyContinue
    Remove-Item -Force .\\dist\\WorkflowAgentRunner.exe -ErrorAction SilentlyContinue
    Remove-Item -Force .\\WorkflowAgentRunner.spec -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path .\\dist\\release -Force | Out-Null

if ($BuildTool -eq "PyInstaller") {
    Write-Host "Building WorkflowAgentRunner.exe with PyInstaller..."
    & $py -m PyInstaller --noconfirm --clean --onefile --name WorkflowAgentRunner --hidden-import main --hidden-import pandas --hidden-import numpy --hidden-import requests_kerberos --hidden-import spnego --collect-all pandas --collect-all numpy .\\run.py

    $builtExe = Resolve-Path .\\dist\\WorkflowAgentRunner.exe
    Copy-Item -LiteralPath $builtExe -Destination .\\dist\\release\\WorkflowAgentRunner.exe -Force
}
else {
    Write-Host "Building WorkflowAgentRunner.exe with Nuitka..."
    & $py -m nuitka --onefile --assume-yes-for-downloads --include-module=main --output-dir=.\\dist\\release --output-filename=WorkflowAgentRunner.exe .\\run.py
}

if (-not (Test-Path -LiteralPath .\\dist\\release\\WorkflowAgentRunner.exe)) {
    throw "Build failed: dist\\release\\WorkflowAgentRunner.exe was not created."
}

Write-Host "Build output: $(Resolve-Path .\\dist\\release\\WorkflowAgentRunner.exe)"
