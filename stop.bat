@echo off
rem Stop the local EDU-IT stack started by start.bat.
rem
rem Usage:  stop.bat
rem
rem Thin wrapper around "tools\e2e_stack.py down": it stops the worker, API and web processes and
rem removes the PostgreSQL and Redis containers recorded in .e2e\stack.json. Containers it did not
rem start (for example a Docker Compose stack) are left running. Linux and macOS: stop.sh does the same.
setlocal

cd /d "%~dp0"

where uv >nul 2>&1 || (echo stop: uv is required ^(https://docs.astral.sh/uv/^) 1>&2 & exit /b 1)

uv run --frozen python tools/e2e_stack.py down || exit /b 1
echo EDU-IT is stopped.
exit /b 0
