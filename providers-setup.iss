; Omni Harness — BYOK cloud providers addon installer (omni-harness-providers-setup.exe)
; Adds NVIDIA NIM and MiniMax to an existing Omni Harness install.
; Compile with Inno Setup 6:  iscc providers-setup.iss

#define MyAppName    "Omni Harness Providers Addon"
#define MyAppVer     "0.1.0"
#define MyAppPublish "tattooinmtl"
#define MyAppURL     "https://github.com/tattooinmtl/omni-harness"

[Setup]
AppId={{5D2E8C41-7A93-4F0B-B6E2-9C1D3A7F4E85}
AppName={#MyAppName}
AppVersion={#MyAppVer}
AppPublisher={#MyAppPublish}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={%USERPROFILE}\.omni-harness
DisableProgramGroupPage=yes
DisableDirPage=no
DirExistsWarning=no
UsePreviousAppDir=yes
OutputBaseFilename=omni-harness-providers-setup
OutputDir=dist
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayName=Omni Harness - BYOK Providers Addon (NVIDIA NIM, MiniMax)
UninstallFilesDir={app}\providers-addon

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
SelectDirLabel3=Select the folder where Omni Harness is installed.
SelectDirBrowseLabel=The addon will be added to the Omni Harness install in this folder. Click Next to continue.

[Files]
Source: "providers-addon\*"; DestDir: "{app}\providers-addon"; \
  Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc,*.pyo"

[UninstallRun]
Filename: "{code:GetPython}"; \
  Parameters: """{app}\providers-addon\install-providers.py"" --uninstall --harness ""{app}\harness"""; \
  WorkingDir: "{app}"; Flags: runhidden; \
  StatusMsg: "Removing providers addon patches..."; RunOnceId: "UninstallProviders"

[Code]
#include "python.iss"

function InitializeSetup: Boolean;
begin
  Result := RequirePython;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and
     not FileExists(AddBackslash(WizardDirValue) + 'harness\omni_agent.py') then begin
    MsgBox('Omni Harness was not found in:' + #13#10 + WizardDirValue + #13#10 + #13#10 +
           'Install Omni Harness first (omni-harness-setup.exe), or pick the folder where it is installed.',
           mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RC: Integer;
begin
  if CurStep <> ssPostInstall then Exit;
  WizardForm.StatusLabel.Caption := 'Patching Omni Harness with the providers addon...';
  if not Exec(PythonExe,
              '"' + ExpandConstant('{app}') + '\providers-addon\install-providers.py" --harness "' +
              ExpandConstant('{app}') + '\harness"',
              ExpandConstant('{app}'), SW_SHOW, ewWaitUntilTerminated, RC) or (RC <> 0) then
    MsgBox('The addon installer failed (exit code ' + IntToStr(RC) + ').', mbError, MB_OK);
end;
