@echo off
REM llmwiki — export ZCode + convert new session transcripts to markdown.
REM Usage: sync.bat [--project <sub>] [--since YYYY-MM-DD] [--include-current] [--force] [--dry-run]
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=%cd%\.venv\Scripts\python.exe"
"%PY%" -m llmwiki sync %*
