; Shared by setup.iss and providers-setup.iss — included inside [Code].

var
  PythonExe: String;

function FindPython: String;
var
  RC: Integer;
begin
  Result := '';
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

function GetPython(Param: String): String;
begin
  if PythonExe = '' then
    PythonExe := FindPython;
  if PythonExe = '' then
    PythonExe := 'python';
  Result := PythonExe;
end;

function RequirePython: Boolean;
begin
  Result := True;
  PythonExe := FindPython;
  if PythonExe <> '' then Exit;
  if MsgBox(
    'Python 3.10 or later is required but was not found on your PATH.' + #13#10 + #13#10 +
    'Please install Python from  https://python.org  and make sure' + #13#10 +
    '"Add Python to PATH" is checked during the Python installer.' + #13#10 + #13#10 +
    'Continue anyway (installation will likely fail)?',
    mbConfirmation, MB_YESNO) = IDNO then
    Result := False
  else
    PythonExe := 'python';
end;
