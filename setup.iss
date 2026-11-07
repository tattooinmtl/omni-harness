; Omni Harness — app installer (omni-harness-setup.exe)
; The BYOK providers addon has its own installer (providers-setup.iss). This
; installer embeds it and runs it when the "providers" task is ticked.
; Compile providers-setup.iss FIRST, then:  iscc setup.iss

#define MyAppName    "Omni Harness"
#define MyAppVer     "0.1.0"
#define MyAppPublish "tattooinmtl"
#define MyAppURL     "https://github.com/tattooinmtl/omni-harness"

[Setup]
AppId={{B7E14A2C-9F38-4D61-A052-1C3E8D5F02AB}
AppName={#MyAppName}
AppVersion={#MyAppVer}
AppPublisher={#MyAppPublish}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={%USERPROFILE}\.omni-harness
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputBaseFilename=omni-harness-setup
OutputDir=dist
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
DisableDirPage=no
DirExistsWarning=no
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "providers"; Description: "Also install the BYOK cloud providers addon (NVIDIA NIM, MiniMax)"; \
  GroupDescription: "Addons:"; Flags: unchecked

[Dirs]
; Kept on uninstall: it holds the user's model files.
Name: "{app}\models"; Flags: uninsneveruninstall

[Files]
Source: "harness\*"; DestDir: "{app}\harness"; Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: "__pycache__,*.pyc,*.pyo,providers_addon"
Source: "pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "models\README.md"; DestDir: "{app}\models"; Flags: onlyifdoesntexist
Source: "dist\omni-harness-providers-setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Tasks: providers

[Icons]
Name: "{group}\Run Omni Harness"; Filename: "{sys}\cmd.exe"; \
  Parameters: "/K ""omni-harness"""; WorkingDir: "{%USERPROFILE}"; \
  Comment: "Open a terminal and start Omni Harness"
Name: "{group}\Uninstall Omni Harness"; Filename: "{uninstallexe}"

[UninstallRun]
; If the providers addon is installed, revert its patches before the app files go.
Filename: "{code:GetPython}"; \
  Parameters: """{app}\providers-addon\install-providers.py"" --uninstall --harness ""{app}\harness"""; \
  WorkingDir: "{app}"; Flags: runhidden; Check: AddonPresent; \
  StatusMsg: "Removing providers addon patches..."; RunOnceId: "UninstallProviders"

[Code]
#include "python.iss"

function AddonPresent: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\providers-addon\install-providers.py'));
end;

function InitializeSetup: Boolean;
begin
  Result := RequirePython;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RC: Integer;
begin
  if CurStep <> ssPostInstall then Exit;

  WizardForm.StatusLabel.Caption := 'Installing omni-harness and its Python dependencies...';
  if not Exec(PythonExe, '-m pip install --upgrade -e "' + ExpandConstant('{app}') + '"',
              ExpandConstant('{app}'), SW_SHOW, ewWaitUntilTerminated, RC) or (RC <> 0) then
    MsgBox('pip install failed (exit code ' + IntToStr(RC) + ').' + #13#10 +
           'You can install manually:' + #13#10 +
           '  cd "' + ExpandConstant('{app}') + '"' + #13#10 +
           '  pip install -e .', mbError, MB_OK);

  WizardForm.StatusLabel.Caption := 'Downloading the Chromium browser used by the browser tools...';
  Exec(PythonExe, '-m playwright install chromium', ExpandConstant('{app}'),
       SW_SHOW, ewWaitUntilTerminated, RC);

  WizardForm.StatusLabel.Caption := 'Carrying your existing settings and keys over to the new config...';
  Exec(PythonExe, '"' + ExpandConstant('{app}') + '\harness\migrate_config.py"',
       ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, RC);

  if WizardIsTaskSelected('providers') then begin
    WizardForm.StatusLabel.Caption := 'Installing the BYOK cloud providers addon...';
    if not Exec(ExpandConstant('{tmp}\omni-harness-providers-setup.exe'),
                '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="' + ExpandConstant('{app}') + '"',
                '', SW_SHOW, ewWaitUntilTerminated, RC) or (RC <> 0) then
      MsgBox('The providers addon failed to install (exit code ' + IntToStr(RC) + ').' + #13#10 +
             'You can run omni-harness-providers-setup.exe from the release page later.',
             mbError, MB_OK);
  end;
end;

{ The app uninstall already reverted the addon's patches; drop the addon's own
  Apps & Features entry too, since its files are gone with the app folder. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteKeyIncludingSubkeys(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{5D2E8C41-7A93-4F0B-B6E2-9C1D3A7F4E85}_is1');
end;
