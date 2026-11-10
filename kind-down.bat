@echo off
rem Delete the local kind cluster and its registry.
rem
rem Usage:  kind-down.bat [-y ^| --yes]
rem
rem Runs "tools\kind_harness.py down", which deletes the edu-it kind cluster and the kind-registry
rem container. Everything in them is lost: the Helm releases, the lab pods and the images pushed to
rem localhost:5001. Recreating them takes several minutes, so this asks first. Pass --yes to skip the
rem question. It does not stop the local app stack: use stop.bat for that.
rem Linux and macOS: kind-down.sh does the same.
setlocal enabledelayedexpansion

cd /d "%~dp0"

set ASSUME_YES=0
for %%A in (%*) do (
    if /i "%%A"=="-y" (set ASSUME_YES=1) else if /i "%%A"=="--yes" (set ASSUME_YES=1) else (
        echo kind-down: unknown option: %%A ^(usage: kind-down.bat [-y ^| --yes]^) 1>&2
        exit /b 1
    )
)

for %%T in (uv kind docker) do (
    where %%T >nul 2>&1 || (echo kind-down: %%T is required but not on the PATH 1>&2 & exit /b 1)
)
docker info >nul 2>&1 || (echo kind-down: the Docker daemon is not reachable; start Docker and retry 1>&2 & exit /b 1)

set CLUSTER_EXISTS=0
kind get clusters 2>nul | findstr /x /c:"edu-it" >nul && set CLUSTER_EXISTS=1
set REGISTRY_EXISTS=0
docker inspect kind-registry >nul 2>&1 && set REGISTRY_EXISTS=1
if "%CLUSTER_EXISTS%%REGISTRY_EXISTS%"=="00" (
    echo kind-down: nothing to remove ^(no edu-it cluster, no kind-registry container^).
    exit /b 0
)

if "%ASSUME_YES%"=="0" (
    echo This deletes the kind cluster edu-it and the registry container kind-registry.
    echo Helm releases, lab pods and images pushed to localhost:5001 are lost.
    set REPLY=
    set /p REPLY=Continue? [y/N] 
    rem !REPLY! (delayed expansion): %REPLY% would be expanded before the prompt ran.
    if /i not "!REPLY!"=="y" if /i not "!REPLY!"=="yes" (
        echo kind-down: cancelled.
        exit /b 1
    )
)

uv run --frozen python tools/kind_harness.py down || exit /b 1
echo The kind cluster and its registry are removed.
exit /b 0
