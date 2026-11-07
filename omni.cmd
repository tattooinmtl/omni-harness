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
rem    omni --llama-dir C:\Llama-master\llama.cpp\latest_bin\bin\Release
rem                                       use your b12189 source build
rem    omni --mcp                         also load the MCP servers in mcp.json
rem    omni selftest                      checks that need no model
rem
rem  Model     : OMNI_MODEL     (default C:\models\NousResearch_Arete-27B-Q4_K_M.gguf)
rem  llama.cpp : OMNI_LLAMA_DIR (default C:\models\llama.cpp, b11873 Clang)
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
