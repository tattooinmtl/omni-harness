#!/usr/bin/env python3
"""
Windows process helpers for the agent's llama-server.

The agent starts a llama-server of its own and has to be sure it is gone
when the agent ends, however the agent ends:

* `/exit` or Ctrl+C runs the normal shutdown path.
* Closing the console window, or a hard kill, does not. On Windows a child
  process does not die with its parent, so the server would survive as an
  orphan holding the model in memory.

The fix is a Windows job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
The server becomes a member of a job whose only handle lives inside the
agent process, so when the agent goes away - cleanly, by a signal, or by
the window closing - Windows kills the job and the server with it.

There is also a stale server: if a previous run was killed hard, its
llama-server is still holding the port and the memory. The agent clears it
on startup, so the server it uses is always one it owns.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes

IS_WINDOWS = os.name == "nt"

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    return kernel32


def kill_on_close(process) -> object | None:
    """Put a child process in a job that dies with this process.

    Returns the job handle, which has to be kept alive for as long as the
    child should live - closing it kills the child. Returns None when the
    job could not be set up, in which case the caller falls back to
    terminating the child explicitly.
    """
    if not IS_WINDOWS or process is None:
        return None
    handle = getattr(process, "_handle", None)
    if handle is None:
        return None
    try:
        kernel32 = _kernel32()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(handle)):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def port_pid(port: int) -> int:
    """The pid listening on a TCP port, or 0."""
    if not IS_WINDOWS:
        return 0
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        ).stdout
    except Exception:
        return 0
    needle = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or "LISTENING" not in line.upper():
            continue
        local = parts[1]
        if local.rsplit(":", 1)[-1] != str(port):
            continue
        try:
            return int(parts[-1])
        except ValueError:
            return 0
    return 0


def process_command_line(pid: int) -> str:
    """The full command line of a process, or an empty string."""
    if not IS_WINDOWS:
        return ""
    try:
        script = (
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
        ).stdout
        return out.strip()
    except Exception:
        return ""


def kill_server_on_port(port: int, llama_dir) -> str:
    """Stop the llama-server listening on our port.

    Only one thing is ever killed: a llama-server that is listening on the
    port and was started from our own llama.cpp build. Anything else is
    left alone.

    Returns a note about what was killed, or an empty string.
    """
    pid = port_pid(port)
    if not pid:
        return ""
    command = process_command_line(pid)
    lowered = command.lower()
    if "llama-server" not in lowered and "llama-server.exe" not in lowered:
        return ""
    if str(llama_dir).lower() not in lowered:
        return ""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return ""
    # Give the port a moment to come free.
    import time

    for _ in range(20):
        if not port_pid(port):
            break
        time.sleep(0.25)
    return f"stopped the llama-server (pid {pid}) on port {port}"


# --------------------------------------------------------------------------
# Watchdog
#
# A job object is the clean way to tie a child's life to its parent's, but
# it cannot be set up when the parent is itself inside a job that forbids
# nesting. A tiny detached watchdog process covers that case: it outlives
# the agent, notices that the agent is gone and stops the server.
# --------------------------------------------------------------------------
_STILL_ACTIVE = 259


def process_alive(pid: int) -> bool:
    """Is a pid still running?"""
    if not IS_WINDOWS or pid <= 0:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def start_watchdog(server_pid: int, parent_pid: int, python: str) -> None:
    """Start a detached watchdog that stops the server if the agent dies.

    It ends as soon as either process is gone, so it never lingers.
    """
    flags = 0
    if IS_WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        subprocess.Popen(
            [python, os.path.abspath(__file__), "--watchdog", str(server_pid), str(parent_pid)],
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception:
        pass


def _watchdog(server_pid: int, parent_pid: int) -> int:
    """Kill the server once the parent is gone."""
    import time

    while process_alive(server_pid):
        if not process_alive(parent_pid):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(server_pid)],
                    capture_output=True, text=True, timeout=30,
                )
            except Exception:
                pass
            return 0
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 4 and sys.argv[1] == "--watchdog":
        sys.exit(_watchdog(int(sys.argv[2]), int(sys.argv[3])))
    sys.exit(0)
