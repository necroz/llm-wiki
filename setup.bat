@echo off
REM llmwiki — one-click installer for Windows.
REM Usage: setup.bat [--graph]
REM Idempotent — safe to re-run.
REM
REM The installer owns a local .venv so the CLI and the wrapper scripts
REM use the same interpreter and do not depend on global site-packages.

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "TARGET=."
if /I "%~1"=="--graph" set "TARGET=.[graph]"

echo ==^> llmwiki setup
echo     root: %cd%

REM 1. Python check
where python >nul 2>&1
if errorlevel 1 (
  echo error: python is required but was not found in PATH
  exit /b 1
)
for /f "delims=" %%v in ('python -c "import sys; print(\".\".join(map(str, sys.version_info[:3])))"') do set PY_VER=%%v
echo     python: !PY_VER!
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 (
  echo error: Python 3.9+ is required, found !PY_VER!
  exit /b 1
)

REM 2. Create the checkout-local virtualenv.
if not exist ".venv\Scripts\python.exe" (
  echo ==^> creating .venv
  python -m venv .venv
  if errorlevel 1 (
    echo error: cannot create .venv
    exit /b 1
  )
)
set "PY=%cd%\.venv\Scripts\python.exe"

REM 3. Install the package into the venv (editable).
set "NEED_INSTALL=0"
"%PY%" -c "import llmwiki, markdown" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"
if /I "%~1"=="--graph" set "NEED_INSTALL=1"
if "!NEED_INSTALL!"=="1" (
  echo ==^> installing llmwiki into .venv
  "%PY%" -m pip install -e "%TARGET%" --quiet
  if errorlevel 1 (
    echo error: pip install failed
    exit /b 1
  )
)

"%PY%" -c "import markdown" >nul 2>&1
if errorlevel 1 (
  echo error: the 'markdown' dependency is missing from .venv
  exit /b 1
)

REM 4. Scaffold raw/ wiki/ site/
"%PY%" -m llmwiki init
if errorlevel 1 exit /b 1

REM 5. Show available adapters
"%PY%" -m llmwiki adapters

REM 6. First sync status (read-only)
echo.
echo ==^> sync status:
"%PY%" -m llmwiki sync --status

echo.
echo ==^> environment check:
"%PY%" -m llmwiki doctor

echo.
echo ================================================================
echo   Setup complete.
echo ================================================================
echo.
echo Next steps:
echo   .venv\Scripts\activate.bat  ^-^- or call .venv\Scripts\llmwiki.exe directly
echo   sync.bat                    ^-^- export ZCode + convert new sessions
echo   build.bat                   ^-^- generate the static HTML site
echo   serve.bat                   ^-^- browse at http://127.0.0.1:8765/
echo   .venv\Scripts\python.exe -m llmwiki zcode export
echo.
echo For ZCode Stop-hook and scheduler setup, see ZCODE.md.
