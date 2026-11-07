@echo off
setlocal EnableExtensions
title Omni - Arete-27B terminal agent (llama.cpp)

rem ===========================================================================
rem  Omni harness - terminal agent for NousResearch Arete-27B on llama.cpp
rem
rem    omni                               interactive session in the last project
rem    omni --workspace C:\work           work in another folder
rem    omni --task "run the tests"        one task, then exit
rem    omni --think low                   thinking on (off is the default)
rem    omni --llama-dir <folder>          use a llama.cpp build in another folder
rem    omni --mcp                         also load the MCP servers in mcp.json
rem    omni selftest                      checks that need no model
rem
rem  Model     : OMNI_MODEL     (default models\ next to this file)
rem  llama.cpp : OMNI_LLAMA_DIR (default llama\ next to this file)
rem  Server    : http://127.0.0.1:8083
rem ===========================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PYTHONUTF8=1"

if /i "%~1"=="selftest" (
    python "%ROOT%\harness\selftest.py"
    exit /b %ERRORLEVEL%
)

python "%ROOT%\harness\omni_agent.py" %*
exit /b %ERRORLEVEL%
