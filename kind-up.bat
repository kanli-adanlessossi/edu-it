@echo off
rem Create (or re-apply) the local kind cluster used by the Kubernetes and Helm tests.
rem
rem Usage:  kind-up.bat [harness options]
rem
rem Builds the lab images when they are missing, then runs "tools\kind_harness.py up": the edu-it
rem cluster from deploy\kind\kind-config.yaml, Calico, metrics-server, ingress-nginx, the CRDs, the
rem local registry at localhost:5001 and the lab images preloaded into every node. It is idempotent:
rem an existing cluster is kept and the components are re-applied.
rem
rem Options are passed to the harness, for example:
rem   kind-up.bat --dry-run                  print the plan, change nothing
rem   kind-up.bat --skip ingress-nginx       leave ingress-nginx out (a network that cannot pull it)
rem   kind-up.bat --skip-lab-images          do not preload the lab images
rem Set ALPINE_MIRROR (for example https://mirror.leaseweb.com/alpine) when the Alpine CDN is
rem unreachable. This is separate from start.bat: it does not start the local app stack.
rem Remove the cluster with kind-down.bat. Linux and macOS: kind-up.sh does the same.
setlocal

cd /d "%~dp0"

for %%T in (uv kind kubectl docker) do (
    where %%T >nul 2>&1 || (echo kind-up: %%T is required but not on the PATH 1>&2 & exit /b 1)
)
docker info >nul 2>&1 || (echo kind-up: the Docker daemon is not reachable; start Docker and retry 1>&2 & exit /b 1)

rem The harness preloads the lab images and refuses to run without them, so build what is missing
rem (not for a dry run, and not when the preload is skipped).
set BUILD_IMAGES=1
set DRY_RUN=0
for %%A in (%*) do (
    if "%%A"=="--dry-run" (set BUILD_IMAGES=0& set DRY_RUN=1)
    if "%%A"=="--skip-lab-images" set BUILD_IMAGES=0
)
if "%BUILD_IMAGES%"=="1" call :ensure_lab_images || exit /b 1

uv run --frozen python tools/kind_harness.py up %* || exit /b 1

if "%DRY_RUN%"=="1" exit /b 0

echo.
kubectl --context kind-edu-it get nodes
echo.
echo The kind cluster is ready.
echo   Context:  kind-edu-it
echo   Registry: localhost:5001 (container kind-registry)
echo Run the kind tests with: uv run pytest -m kind --no-cov tests/k8s
echo Remove the cluster with kind-down.bat
exit /b 0

rem A subroutine, not a parenthesized block: cmd expands %VAR% in a block when the block is
rem parsed, so a variable set inside the block would not be seen by a test in the same block.
:ensure_lab_images
set NEED_BUILD=0
docker image inspect edu-it/lab-linux-base:dev >nul 2>&1 || set NEED_BUILD=1
docker image inspect edu-it/lab-python:dev >nul 2>&1 || set NEED_BUILD=1
if "%NEED_BUILD%"=="0" exit /b 0
echo ==^> lab images ^(docker buildx bake --load^)
pushd lab-images
docker buildx bake --load
set BAKE_EXIT=%ERRORLEVEL%
popd
exit /b %BAKE_EXIT%
