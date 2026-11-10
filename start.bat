@echo off
rem Start the whole EDU-IT stack locally: PostgreSQL and Redis containers, migrations, content
rem sync, the lab images, the worker, the API (Docker sandbox provider) and the web app.
rem
rem Usage:  start.bat
rem
rem Wraps "tools\e2e_stack.py up", which records what it started in .e2e\stack.json so that
rem stop.bat removes exactly that and nothing else, then "tools\dev_seed.py", which creates the dev
rem admin and approves the synced content. The admin's email and password come from DEV_ADMIN_EMAIL
rem and DEV_ADMIN_PASSWORD or the git-ignored .env.dev-admin (template: .env.dev-admin.example);
rem without them the seeding is skipped. Set ALPINE_MIRROR (for example
rem https://mirror.leaseweb.com/alpine) when the Alpine CDN is unreachable.
rem The worker, API and web processes keep running after this script returns; stop them with
rem stop.bat. Linux and macOS: start.sh does the same.
setlocal

cd /d "%~dp0"

if not defined READY_TIMEOUT_S set READY_TIMEOUT_S=180

where uv >nul 2>&1 || (echo start: uv is required ^(https://docs.astral.sh/uv/^) 1>&2 & exit /b 1)
where npm >nul 2>&1 || (echo start: Node.js with npm is required 1>&2 & exit /b 1)
where curl.exe >nul 2>&1 || (echo start: curl.exe is required 1>&2 & exit /b 1)
docker info >nul 2>&1 || (echo start: the Docker daemon is not reachable; start Docker and retry 1>&2 & exit /b 1)

if not exist "apps\web\node_modules\" (
    echo ==^> web dependencies ^(npm ci^)
    call npm ci --prefix apps/web || exit /b 1
)

uv run --frozen python tools/e2e_stack.py up || exit /b 1

rem Dev admin and content approval (credentials from DEV_ADMIN_* or the git-ignored .env.dev-admin;
rem skipped with a message when none are configured).
uv run --frozen python tools/dev_seed.py || exit /b 1

rem "up" returns once the processes are started; wait until each one answers its probe.
call :wait_ready api http://localhost:8000/ready || exit /b 1
call :wait_ready worker http://localhost:8001/ready || exit /b 1
call :wait_ready web http://localhost:3000/api/healthz || exit /b 1

echo.
echo EDU-IT is running.
echo   Web:    http://localhost:3000  (/fr and /en)
echo   API:    http://localhost:8000
echo   Worker: http://localhost:8001/ready
echo Logs: .e2e\mail.log (API and the console mailer's verification links), .e2e\worker.log, .e2e\web.log
echo Stop everything with stop.bat
exit /b 0

:wait_ready
set /a WAITED=0
echo ==^> waiting for %1 (%2)
:wait_loop
curl.exe -fsS -o nul --max-time 5 %2 >nul 2>&1 && (echo     %1 ready & exit /b 0)
if %WAITED% geq %READY_TIMEOUT_S% (
    echo start: %1 did not become ready within %READY_TIMEOUT_S%s; see the logs under .e2e\ and run stop.bat 1>&2
    exit /b 1
)
timeout /t 2 /nobreak >nul
set /a WAITED+=2
goto wait_loop
