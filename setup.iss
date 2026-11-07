; Omni Harness — Windows Installer
; Compile with Inno Setup 6:  iscc setup.iss
; Or let the GitHub Actions release workflow build it automatically.

#define MyAppName    "Omni Harness"
#define MyAppVer     "0.1.0"
#define MyAppPublish "tattooinmtl"
#define MyAppURL     "https://github.com/tattooinmtl/omni-harness"

; ---------------------------------------------------------------------------
[Setup]
AppId={{B7E14A2C-9F38-4D61-A052-1C3E8D5F02AB}
AppName={#MyAppName}
AppVersion={#MyAppVer}
AppPublisher={#MyAppPublish}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={userhome}\.omni-harness
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
UninstallDisplayName={#MyAppName}

; ---------------------------------------------------------------------------
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ---------------------------------------------------------------------------
[Types]
Name: "full";    Description: "Full installation  (core + BYOK cloud providers)"
Name: "minimal"; Description: "Minimal installation  (core / local llama.cpp only)"
Name: "custom";  Description: "Custom"; Flags: iscustom

; ---------------------------------------------------------------------------
[Components]
Name: "core";      Description: "Omni Harness core (required)"; Types: full minimal custom; Flags: fixed
Name: "providers"; Description: "BYOK cloud providers addon — NVIDIA NIM & MiniMax"; Types: full; ExtraDiskSpaceRequired: 51200

; ---------------------------------------------------------------------------
; Source files — only project code, no models / caches / local state
; ---------------------------------------------------------------------------
[Files]
; Core harness package
Source: "harness\*";       DestDir: "{app}\harness";       Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: "__pycache__,*.pyc,*.pyo,providers_addon"

; Package manifest (needed for pip install -e)
Source: "pyproject.toml";  DestDir: "{app}"; Flags: ignoreversion

; Providers addon source (bundled always; only installed when component selected)
Source: "providers-addon\*"; DestDir: "{app}\providers-addon"; \
  Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: "__pycache__,*.pyc,*.pyo"

; ---------------------------------------------------------------------------
[Icons]
Name: "{group}\Run Omni Harness";    Filename: "{sys}\cmd.exe"; \
  Parameters: "/K ""omni-harness"""; WorkingDir: "{userhome}"; \
  Comment: "Open a terminal and start Omni Harness"
Name: "{group}\Uninstall Omni Harness"; Filename: "{uninstallexe}"

; ---------------------------------------------------------------------------
[UninstallRun]
; Revert addon patches before the files are deleted
Filename: "{code:GetPython}"; \
  Parameters: """{app}\providers-addon\install-providers.py"" --uninstall --harness ""{app}\harness"""; \
  WorkingDir: "{app}\providers-addon"; \
  StatusMsg: "Removing providers addon patches..."; \
  RunOnceId: "UninstallProviders"

; ---------------------------------------------------------------------------
[Code]

var
  PythonExe: String;

(* -----------------------------------------------------------------------
   Find a usable Python 3.10+ executable.
   Preference order: py launcher → python → python3
   ----------------------------------------------------------------------- *)
function FindPython: String;
var
  RC: Integer;
begin
  Result := '';
  // py launcher (Windows Python Launcher — most reliable)
  if Exec('py', '-3 --version', '', SW_HIDE, ewWaitUntilTerminated, RC) and (RC = 0) then begin
    Result := 'py';
    Exit;
  end;
  if Exec('python', '--version', '', SW_HIDE, ewWaitUntilTerminated, RC) and (RC = 0) then begin
    Result := 'python';
    Exit;
  end;
  if Exec('python3', '--version', '', SW_HIDE, ewWaitUntilTerminated, RC) and (RC = 0) then begin
    Result := 'python3';
    Exit;
  end;
end;

(* Called by {code:GetPython} in [Run] sections *)
function GetPython(Param: String): String;
begin
  if PythonExe = '' then
    PythonExe := FindPython;
  if PythonExe = '' then
    PythonExe := 'python';
  Result := PythonExe;
end;

(* -----------------------------------------------------------------------
   Pre-install check: Python must be present.
   ----------------------------------------------------------------------- *)
function InitializeSetup: Boolean;
begin
  PythonExe := FindPython;
  if PythonExe = '' then begin
    if MsgBox(
      'Python 3.10 or later is required but was not found on your PATH.' + #13#10 + #13#10 +
      'Please install Python from  https://python.org  and make sure' + #13#10 +
      '"Add Python to PATH" is checked during the Python installer.' + #13#10 + #13#10 +
      'Continue anyway (installation will likely fail)?',
      mbConfirmation, MB_YESNO) = IDNO then begin
      Result := False;
      Exit;
    end;
    PythonExe := 'python';  // best-effort fallback
  end;
  Result := True;
end;

(* -----------------------------------------------------------------------
   After files are laid down, run pip install and optionally the addon.
   Show a visible cmd window so users can see progress / errors.
   ----------------------------------------------------------------------- *)
procedure CurStepChanged(CurStep: TSetupStep);
var
  RC: Integer;
  PipArgs, AddonArgs: String;
begin
  if CurStep <> ssPostInstall then Exit;

  WizardForm.StatusLabel.Caption := 'Installing omni-harness and its Python dependencies...';
  PipArgs := '-m pip install --upgrade -e "' + ExpandConstant('{app}') + '"';
  if not Exec(PythonExe, PipArgs, ExpandConstant('{app}'), SW_SHOW,
              ewWaitUntilTerminated, RC) or (RC <> 0) then begin
    MsgBox(
      'pip install failed (exit code ' + IntToStr(RC) + ').' + #13#10 +
      'You can install manually:' + #13#10 +
      '  cd "' + ExpandConstant('{app}') + '"' + #13#10 +
      '  pip install -e .',
      mbError, MB_OK);
  end;

  WizardForm.StatusLabel.Caption := 'Downloading the Chromium browser used by the browser tools...';
  Exec(PythonExe, '-m playwright install chromium', ExpandConstant('{app}'),
       SW_SHOW, ewWaitUntilTerminated, RC);

  // Providers addon
  if WizardIsComponentSelected('providers') then begin
    WizardForm.StatusLabel.Caption := 'Installing BYOK cloud providers addon...';
    AddonArgs := '"' + ExpandConstant('{app}') + '\providers-addon\install-providers.py"' +
                 ' --harness "' + ExpandConstant('{app}') + '\harness"';
    Exec(PythonExe, AddonArgs, ExpandConstant('{app}\providers-addon'),
         SW_SHOW, ewWaitUntilTerminated, RC);
  end;
end;
