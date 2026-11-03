<#
.SYNOPSIS
  Checks the Live_Run prerequisites (ai-content task 28.1) and prints PASS/FAIL/WARN per item.

.DESCRIPTION
  Read-only: it never merges, cherry-picks, calls a model or prints a secret value. Items:
    1. ADR 0007 addendum "Vendor selection (decision D1)" exists and is Status Accepted.
    2. Vendor adapter bedrock_claude.py exists and its contract tests pass (skip with -SkipTests).
    3. content/networking-basics/brief.json exists on the base ref (git ls-tree; optional -Fetch).
    4. Generator base branch resolved as the generator does (GENERATOR_BASE_BRANCH, else
       origin/HEAD, else init.defaultBranch if that local branch exists) and the brief is on the
       LOCAL ref the generator reads.
    5. Docker daemon reachable (docker info).
    6. Environment valid per tools/live-run-env.ps1 (dot-sourced, no duplicated rules, no prompt;
       stored credential lookup via SecretManagement / Credential Manager still applies).
    7. Working tree clean (WARN only; task 28.2 needs a clean tree).
  Exits 1 if any item is FAIL, otherwise 0.

      .\tools\check_live_prereqs.ps1
      .\tools\check_live_prereqs.ps1 -Fetch -SkipTests
      .\tools\check_live_prereqs.ps1 -EnvFile C:\secure\my.env -SecretName my-key-name

.PARAMETER BaseRef
  Ref that must contain the brief. Default: origin/main

.PARAMETER Fetch
  Run `git fetch origin` first (updates remote-tracking refs only).

.PARAMETER SkipTests
  Do not run the Bedrock contract tests.

.PARAMETER EnvFile
  Env file passed to live-run-env.ps1. Default: .env.live

.PARAMETER SecretName
  Stored credential name passed to live-run-env.ps1.
#>
[CmdletBinding()]
param(
    [string]$BaseRef = 'origin/main',
    [switch]$Fetch,
    [switch]$SkipTests,
    [string]$EnvFile = '.env.live',
    [string]$SecretName = 'edu-it-llm-provider-api-key'
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$results = [System.Collections.Generic.List[object]]::new()

function Add-Result([string]$Status, [string]$Item, [string]$Reason, [bool]$Owner = $false) {
    $results.Add([pscustomobject]@{ Status = $Status; Item = $Item; Owner = $Owner })
    $color = @{ PASS = 'Green'; FAIL = 'Red'; WARN = 'Yellow' }[$Status]
    Write-Host ('[{0}] {1}: {2}' -f $Status, $Item, $Reason) -ForegroundColor $color
}

Push-Location $repo
try {
    # 1. ADR addendum
    $adrRel = 'docs/adr/0007-llm-provider-and-data-residency.md'
    $adr = Join-Path $repo $adrRel
    if (-not (Test-Path -LiteralPath $adr)) {
        Add-Result FAIL 'ADR 0007 addendum' "$adrRel not found"
    } else {
        $lines = Get-Content -LiteralPath $adr
        $idx = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match '^##\s.*Vendor selection \(decision D1\)') { $idx = $i; break }
        }
        if ($idx -lt 0) {
            Add-Result FAIL 'ADR 0007 addendum' 'section "Vendor selection (decision D1)" not found'
        } else {
            $status = $null
            for ($j = $idx + 1; $j -lt $lines.Count -and $lines[$j] -notmatch '^##\s'; $j++) {
                if ($lines[$j] -match '^\s*-\s*\*\*Status:\*\*\s*(\S+)') { $status = $Matches[1].TrimEnd('.', ','); break }
            }
            if ($status -eq 'Accepted') { Add-Result PASS 'ADR 0007 addendum' 'D1 section present, Status Accepted' }
            else { Add-Result FAIL 'ADR 0007 addendum' "D1 section status is '$status', expected Accepted" $true }
        }
    }

    # 2. Vendor adapter + contract tests
    $adapterRel = 'packages/infrastructure/edu_it_infrastructure/llm/vendors/bedrock_claude.py'
    if (-not (Test-Path -LiteralPath (Join-Path $repo $adapterRel))) {
        Add-Result FAIL 'Vendor adapter' "$adapterRel not found"
    } elseif ($SkipTests) {
        Add-Result WARN 'Vendor adapter' 'file exists; contract tests skipped (-SkipTests)'
    } else {
        $venvPy = Join-Path $repo '.venv\Scripts\python.exe'
        $testArgs = @('packages/infrastructure/tests/test_llm_bedrock_claude.py', '-q', '--no-cov', '-p', 'no:cacheprovider')
        $exe = $null; $pre = @()
        if (Test-Path -LiteralPath $venvPy) { $exe = $venvPy; $pre = @('-m', 'pytest') }
        elseif (Get-Command uv -ErrorAction SilentlyContinue) { $exe = 'uv'; $pre = @('run', '--frozen', 'pytest') }
        if (-not $exe) {
            Add-Result FAIL 'Vendor adapter' 'file exists but no .venv or uv found to run the tests'
        } else {
            $out = & $exe @pre @testArgs 2>&1 | Out-String
            $code = $LASTEXITCODE
            $summary = ($out -split "`r?`n" | Where-Object { $_ -match '\b(passed|failed|error)' } | Select-Object -Last 1)
            if ($code -eq 0) { Add-Result PASS 'Vendor adapter' "file exists, contract tests pass ($($summary.Trim()))" }
            else { Add-Result FAIL 'Vendor adapter' "contract tests failed (exit $code): $summary" }
        }
    }

    # 3. Brief on the base ref
    $briefRel = 'content/networking-basics/brief.json'
    $gitOk = $true
    if ($Fetch) {
        $null = & git fetch origin 2>&1
        if ($LASTEXITCODE -ne 0) { Add-Result WARN 'git fetch' "git fetch origin failed (exit $LASTEXITCODE); using local refs" }
    }
    $null = & git rev-parse --verify --quiet "$BaseRef^{commit}" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Add-Result FAIL 'Brief on base ref' "base ref '$BaseRef' not found (try -Fetch or -BaseRef)"
    } else {
        $onBase = & git ls-tree --name-only $BaseRef -- $briefRel 2>$null
        if ($onBase) {
            Add-Result PASS 'Brief on base ref' "$briefRel exists on $BaseRef"
        } else {
            $onHead = & git ls-tree --name-only HEAD -- $briefRel 2>$null
            if ($onHead) {
                Add-Result FAIL 'Brief on base ref' "$briefRel is on the current branch but not on $BaseRef; the owner must merge it (not done here)" $true
            } else {
                Add-Result FAIL 'Brief on base ref' "$briefRel is on neither $BaseRef nor the current branch; the owner must provide and merge it" $true
            }
        }
    }

    # 3b. Generator base branch: mirrors apps/worker/edu_it_worker/ai/cli.py (env var, else
    # SubprocessGitWorkspace.default_branch(): origin/HEAD, then init.defaultBranch, each only
    # if refs/heads/<name> exists). The generator reads the brief from the LOCAL ref named
    # <base> (git rev-parse/ls-tree <base>), not from origin/<base>.
    $baseEnvName = 'GENERATOR_BASE_BRANCH'
    $baseItem = 'Generator base branch'
    $baseName = $null; $baseSource = $null
    $envBase = [Environment]::GetEnvironmentVariable($baseEnvName)
    if ($envBase) {
        $baseName = $envBase; $baseSource = "env $baseEnvName"
    } else {
        $oh = & git symbolic-ref --short refs/remotes/origin/HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $oh) {
            $cand = ("$oh".Trim() -replace '^origin/', '')
            if ($cand) {
                $null = & git rev-parse --verify --quiet "refs/heads/$cand^{commit}" 2>&1
                if ($LASTEXITCODE -eq 0) { $baseName = $cand; $baseSource = 'origin/HEAD' }
            }
        }
        if (-not $baseName) {
            $cfg = & git config --get init.defaultBranch 2>$null
            if ($LASTEXITCODE -eq 0 -and $cfg) {
                $cand = "$cfg".Trim()
                $null = & git rev-parse --verify --quiet "refs/heads/$cand^{commit}" 2>&1
                if ($LASTEXITCODE -eq 0) { $baseName = $cand; $baseSource = 'init.defaultBranch' }
            }
        }
    }
    if (-not $baseName) {
        Add-Result FAIL $baseItem "no integration branch found; set $baseEnvName (the generator's integration-branch variable)" $true
    } else {
        $null = & git rev-parse --verify --quiet "$baseName^{commit}" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Add-Result FAIL $baseItem "'$baseName' (from $baseSource) does not resolve to a commit in this repo; fix $baseEnvName" $true
        } else {
            $onLocal = & git ls-tree --name-only $baseName -- $briefRel 2>$null
            if ($onLocal) {
                Add-Result PASS $baseItem "$baseName (source: $baseSource); $briefRel exists on local ref '$baseName', which the generator reads"
            } else {
                $remoteRef = "origin/$baseName"
                $onRemote = $null
                $null = & git rev-parse --verify --quiet "refs/remotes/$remoteRef^{commit}" 2>&1
                if ($LASTEXITCODE -eq 0) { $onRemote = & git ls-tree --name-only "refs/remotes/$remoteRef" -- $briefRel 2>$null }
                if ($onRemote) {
                    Add-Result FAIL $baseItem "$baseName (source: $baseSource): local '$baseName' lacks $briefRel but $remoteRef has it; the generator reads the local branch, so the owner must fetch and fast-forward local '$baseName' (not done here)" $true
                } else {
                    Add-Result FAIL $baseItem "$baseName (source: $baseSource): $briefRel is missing on local '$baseName' and on $remoteRef; the owner must provide and merge it" $true
                }
            }
        }
    }

    # 4. Docker
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Add-Result FAIL 'Docker daemon' 'docker CLI not found' $true
    } else {
        $null = & docker info 2>&1
        if ($LASTEXITCODE -eq 0) { Add-Result PASS 'Docker daemon' 'reachable (docker info)' }
        else { Add-Result FAIL 'Docker daemon' 'not reachable; start Docker Desktop' $true }
    }

    # 5. Environment: reuse live-run-env.ps1 by dot-sourcing inside a function (own scope).
    function Invoke-EnvCheck {
        $envScript = Join-Path $PSScriptRoot 'live-run-env.ps1'
        $global:LASTEXITCODE = 0
        $captured = . $envScript -EnvFile $EnvFile -SecretName $SecretName -NoPrompt *>&1 | ForEach-Object { "$_" }
        return @{ Code = $global:LASTEXITCODE; Lines = @($captured) }
    }
    $envRes = Invoke-EnvCheck
    if ($envRes.Code -eq 0) {
        $credName = if ($env:LLM_PROVIDER_API_KEY) { 'LLM_PROVIDER_API_KEY' } else { 'LLM_PROVIDER_API_KEY_SECRET_ID' }
        $src = ($envRes.Lines | Where-Object { $_ -match 'loaded from' } | Select-Object -First 1)
        $extra = if ($src) { "; $src" } else { '' }
        Add-Result PASS 'Environment' "valid; credential variable $credName set (value not shown)$extra" 
    } else {
        $probs = @($envRes.Lines | Where-Object { $_ -match '^\s+-\s' } | ForEach-Object { $_.Trim().TrimStart('-').Trim() })
        if (-not $probs) { $probs = @('see tools/live-run-env.ps1 output') }
        $ownerKey = [bool]($probs | Where-Object { $_ -match 'neither LLM_PROVIDER_API_KEY' })
        Add-Result FAIL 'Environment' ($probs -join '; ') $ownerKey
    }

    # 6. Working tree
    $porcelain = & git status --porcelain 2>$null
    if ($LASTEXITCODE -ne 0) { Add-Result WARN 'Working tree' 'git status failed' }
    elseif ($porcelain) { Add-Result WARN 'Working tree' "not clean ($(@($porcelain).Count) changed paths); task 28.2 needs a clean tree" }
    else { Add-Result PASS 'Working tree' 'clean' }
}
finally { Pop-Location }

$fail = @($results | Where-Object Status -eq 'FAIL')
$warn = @($results | Where-Object Status -eq 'WARN')
$pass = @($results | Where-Object Status -eq 'PASS')
Write-Host ''
$summaryColor = 'Green'
if ($fail.Count) { $summaryColor = 'Red' } elseif ($warn.Count) { $summaryColor = 'Yellow' }
Write-Host ("Summary: {0} PASS, {1} WARN, {2} FAIL" -f $pass.Count, $warn.Count, $fail.Count) -ForegroundColor $summaryColor
$owner = @($fail | Where-Object Owner | ForEach-Object Item)
if ($owner) { Write-Host "Owner actions needed for: $($owner -join ', ')" -ForegroundColor Yellow }
if ($fail.Count) { exit 1 }
exit 0
