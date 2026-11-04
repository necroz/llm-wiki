@echo off
REM llmwiki — build the static HTML site.
REM Usage: build.bat [--synthesize] [--out <dir>]
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=%cd%\.venv\Scripts\python.exe"
"%PY%" -m llmwiki build %*
