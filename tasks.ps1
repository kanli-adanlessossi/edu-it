<#
EDU-IT task runner (PowerShell wrapper).

Cross-platform companion to the Makefile that exposes the same uniform entry
points — lint, format, typecheck, test — for Windows environments without
`make`. CI and pre-commit invoke these so every caller uses the same commands.

Usage:  ./tasks.ps1 <lint|format|typecheck|test>

The concrete per-language tool invocations are wired by a later task; until then
each command fans out to no-op sub-steps that print what they will run, so the
entry points exist and are callable now.

Convention: a command exits non-zero (reporting the offending location) on a
lint violation, type error, or test failure, and exits zero when clean.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('lint', 'format', 'typecheck', 'test', 'help')]
    [string]$Task = 'help'
)

$ErrorActionPreference = 'Stop'

function Show-Usage {
    Write-Host 'Usage: ./tasks.ps1 <lint|format|typecheck|test>'
    Write-Host '  lint       Run all linters (TypeScript + Python)'
    Write-Host '  format     Run all formatters (TypeScript + Python)'
    Write-Host '  typecheck  Run all type checks (TypeScript + Python)'
    Write-Host '  test       Run all test suites (TypeScript + Python)'
}

$WebDir = 'apps/web'

function Invoke-WebTask([string]$Name) {
    # Real apps/web TypeScript tooling via its package.json scripts. The step
    # exits non-zero (reporting the offending file path and rule/location) on a
    # violation, type error, or test failure. `format` runs the non-mutating
    # check so it is safe in CI and pre-commit.
    $script = if ($Name -eq 'format') { 'format:check' } else { $Name }
    npm --prefix $WebDir run $script
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

# Command prefix that runs the workspace's pinned Python toolchain (ruff, mypy,
# pytest). Override via the UV environment variable to use tools on PATH.
$UvPrefix = if ($env:UV) { $env:UV } else { 'uv run' }

function Invoke-PyTask([string]$Name) {
    # Real Python tooling across all five components (apps/api, apps/worker,
    # packages/domain, packages/application, packages/infrastructure). Tool
    # configuration and the pinned versions live in the root pyproject.toml,
    # which scopes ruff/mypy/pytest to those directories. Exit-status contract
    # (Req 1.4-1.7): each command exits non-zero and reports the offending
    # location on a violation / type error / test failure, and exits zero when
    # clean. `format` rewrites in place.
    $cmd = switch ($Name) {
        'lint'      { 'ruff check .' }
        'format'    { 'ruff format .' }
        'typecheck' { 'mypy' }
        'test'      { 'pytest' }
    }
    $full = "$UvPrefix $cmd"
    Invoke-Expression $full
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Invoke-Task([string]$Name) {
    # web = TypeScript tooling in apps/web (real); py = Python tooling in
    # apps/api, apps/worker, packages/*.
    Invoke-WebTask $Name
    Write-Host "[$Name-py] Python $Name (apps/api, apps/worker, packages/*)"
    Invoke-PyTask $Name
}

switch ($Task) {
    'help' { Show-Usage }
    default { Invoke-Task $Task }
}
