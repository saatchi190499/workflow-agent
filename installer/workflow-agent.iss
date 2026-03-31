#define AppName "Workflow Agent"
#define AppVersion "1.0.0"
#define AppPublisher "ProdCast"
#define AppExeName "WorkflowAgentRunner.exe"
#ifndef SourceRoot
  #define SourceRoot "."
#endif

[Setup]
AppId={{6D4A8D1A-A4B2-4D72-ABF8-0D6A3E589A1F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\WorkflowAgent
DisableProgramGroupPage=yes
OutputDir={#SourceRoot}\dist\installer
OutputBaseFilename=WorkflowAgentSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SourceRoot}\dist\release\WorkflowAgentRunner.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\service\install_exe_service.ps1"; DestDir: "{app}"; DestName: "install_exe_service.ps1"; Flags: ignoreversion
Source: "{#SourceRoot}\scripts\service\uninstall_exe_service.ps1"; DestDir: "{app}"; DestName: "uninstall_exe_service.ps1"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; Parameters: "{code:GetInstallArgs}"; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\uninstall_exe_service.ps1"" -InstallDir ""{app}"" -ServiceName ""WorkflowAgentService"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveWorkflowAgentService"

[Code]
var
  UrlPage: TInputQueryWizardPage;
  OptPage: TInputOptionWizardPage;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = UrlPage.ID then
  begin
    if Trim(UrlPage.Values[0]) = '' then
    begin
      MsgBox('Main server URL is required.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure InitializeWizard;
begin
  UrlPage := CreateInputQueryPage(
    wpSelectDir,
    'Connection Settings',
    'Configure Workflow Agent backend connection',
    'Set your ProdCast API base URL.'
  );
  UrlPage.Add('Main server URL (example: https://btlweb/api):', False);
  UrlPage.Values[0] := 'https://btlweb/api';

  OptPage := CreateInputOptionPage(
    UrlPage.ID,
    'Agent Options',
    'Optional runtime flags',
    'Select options as needed.',
    False,
    False
  );
  OptPage.Add('Disable SSL verify (self-signed certificate)');
  OptPage.Add('Disable remote imports');
end;

function GetInstallArgs(Param: string): string;
begin
  Result :=
    '-ExecutionPolicy Bypass -NoProfile -File "' + ExpandConstant('{app}\install_exe_service.ps1') +
    '" -RunnerExePath "' + ExpandConstant('{app}\WorkflowAgentRunner.exe') +
    '" -MainServerUrl "' + UrlPage.Values[0] + '"';

  if OptPage.Values[0] then
    Result := Result + ' -DisableSslVerify';

  if OptPage.Values[1] then
    Result := Result + ' -DisableRemoteImports';
end;
