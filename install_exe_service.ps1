param(
    [Parameter(Mandatory = $true)]
    [string]$RunnerExePath,

    [string]$InstallDir = "C:\Program Files\WorkflowAgent",
    [string]$ServiceName = "WorkflowAgentService",
    [string]$MainServerUrl = "https://btlweb/api",

    [string]$ApiKey = "",
    [string]$AuthToken = "",
    [string]$RefreshToken = "",
    [string]$Username = "",
    [string]$Password = "",

    [switch]$DisableRemoteImports,
    [switch]$DisablePetex,
    [switch]$DisableSslVerify,

    [string]$WinSWUrl = "https://github.com/winsw/winsw/releases/latest/download/WinSW-x64.exe"
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Escape-Xml {
    param([string]$Value)
    if ($null -eq $Value) { return "" }
    return [System.Security.SecurityElement]::Escape($Value)
}

function Wait-AgentReady {
    param([int]$TimeoutSeconds = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:9000/variables/" -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) { return $true }
        }
        catch {}
        Start-Sleep -Seconds 1
    }
    return $false
}

if (-not (Test-IsAdmin)) {
    throw "Run this script in Administrator PowerShell."
}

$runner = Resolve-Path -LiteralPath $RunnerExePath
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Runner exe not found: $RunnerExePath"
}

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

$runnerDest = Join-Path $InstallDir "WorkflowAgentRunner.exe"
$svcExe = Join-Path $InstallDir "$ServiceName.exe"
$svcXml = Join-Path $InstallDir "$ServiceName.xml"
$envFile = Join-Path $InstallDir "service.env"

Write-Host "[1/6] Copying runner exe..."
Copy-Item -LiteralPath $runner -Destination $runnerDest -Force

Write-Host "[2/6] Downloading WinSW wrapper..."
Invoke-WebRequest -Uri $WinSWUrl -OutFile $svcExe

$sslVerifyValue = if ($DisableSslVerify) { "0" } else { "1" }
$disableRemoteImportsValue = if ($DisableRemoteImports) { "1" } else { "0" }
$disablePetexValue = if ($DisablePetex) { "1" } else { "0" }

$xml = @"
<service>
  <id>$ServiceName</id>
  <name>$ServiceName</name>
  <description>Workflow Agent local service.</description>
  <executable>%BASE%\\WorkflowAgentRunner.exe</executable>
  <workingdirectory>%BASE%</workingdirectory>
  <stoptimeout>15 sec</stoptimeout>
  <log mode="append"/>
  <env name="WORKFLOW_AGENT_MAIN_SERVER_URL" value="$(Escape-Xml $MainServerUrl)"/>
  <env name="WORKFLOW_AGENT_SSL_VERIFY" value="$sslVerifyValue"/>
  <env name="WORKFLOW_AGENT_DISABLE_REMOTE_IMPORTS" value="$disableRemoteImportsValue"/>
  <env name="WORKFLOW_AGENT_DISABLE_PETEX" value="$disablePetexValue"/>
"@
if ($ApiKey) { $xml += "`n  <env name=`"WORKFLOW_AGENT_API_KEY`" value=`"$(Escape-Xml $ApiKey)`"/>" }
if ($AuthToken) { $xml += "`n  <env name=`"WORKFLOW_AGENT_AUTH_TOKEN`" value=`"$(Escape-Xml $AuthToken)`"/>" }
if ($RefreshToken) { $xml += "`n  <env name=`"WORKFLOW_AGENT_REFRESH_TOKEN`" value=`"$(Escape-Xml $RefreshToken)`"/>" }
if ($Username) { $xml += "`n  <env name=`"WORKFLOW_AGENT_USERNAME`" value=`"$(Escape-Xml $Username)`"/>" }
if ($Password) { $xml += "`n  <env name=`"WORKFLOW_AGENT_PASSWORD`" value=`"$(Escape-Xml $Password)`"/>" }
$xml += "`n</service>`n"
$xml | Set-Content -LiteralPath $svcXml -Encoding UTF8

$envLines = @(
    "WORKFLOW_AGENT_MAIN_SERVER_URL=$MainServerUrl",
    "WORKFLOW_AGENT_SSL_VERIFY=$sslVerifyValue",
    "WORKFLOW_AGENT_DISABLE_REMOTE_IMPORTS=$disableRemoteImportsValue",
    "WORKFLOW_AGENT_DISABLE_PETEX=$disablePetexValue"
)
if ($ApiKey) { $envLines += "WORKFLOW_AGENT_API_KEY=$ApiKey" }
if ($AuthToken) { $envLines += "WORKFLOW_AGENT_AUTH_TOKEN=$AuthToken" }
if ($RefreshToken) { $envLines += "WORKFLOW_AGENT_REFRESH_TOKEN=$RefreshToken" }
if ($Username) { $envLines += "WORKFLOW_AGENT_USERNAME=$Username" }
if ($Password) { $envLines += "WORKFLOW_AGENT_PASSWORD=$Password" }
$envLines | Set-Content -LiteralPath $envFile -Encoding UTF8

Write-Host "[3/6] Removing existing service if present..."
try { & $svcExe stop } catch {}
try { & $svcExe uninstall } catch {}

Write-Host "[4/6] Installing service..."
& $svcExe install

Write-Host "[5/6] Starting service..."
& $svcExe start

Write-Host "[6/6] Health check..."
if (Wait-AgentReady -TimeoutSeconds 90) {
    Write-Host "Service is running at http://127.0.0.1:9000"
} else {
    Write-Warning "Service installed but health check failed. Check $InstallDir\\$ServiceName*.log"
}




