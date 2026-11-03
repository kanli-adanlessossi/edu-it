<#
.SYNOPSIS
  Loads the Live_Run environment (ai-content task 28.1, ADR 0007 addendum) into this process.

.DESCRIPTION
  Reads KEY=VALUE lines from a git-ignored env file (default .env.live at the repo root),
  fills in the non-secret ADR 0007 defaults for anything still unset, and validates the result.
  Only variable NAMES are ever printed, never values.

  DOT-SOURCE it so the variables persist in your shell:

      . .\tools\live-run-env.ps1
      . .\tools\live-run-env.ps1 -EnvFile C:\secure\my.env

  Run normally (without the leading dot) the variables vanish when the script ends, so that form
  is only useful with -Run:

      .\tools\live-run-env.ps1 -Run --help      # runs: edu-it-generate --help

  On problems it prints a list (no secrets), sets $LASTEXITCODE to 1, and returns (dot-sourced)
  or exits 1 (executed). Variables already set in the process are never overwritten by defaults;
  values in the env file do override the process.

.PARAMETER SecretName
  Name of the stored credential (default edu-it-llm-provider-api-key). When neither
  LLM_PROVIDER_API_KEY nor LLM_PROVIDER_API_KEY_SECRET_ID is set, the key is looked up in this order:
  Microsoft.PowerShell.SecretManagement vault (if the module and a vault exist), then Windows
  Credential Manager (same target name), then a hidden Read-Host prompt. Only the store name is
  printed. See tools/live-run-credential.ps1.

.PARAMETER StoreKey
  Prompt for the key (hidden) and store it in the vault / Credential Manager, then exit. The key is
  never written to a file, profile or output. -Store picks SecretManagement or CredentialManager.

.PARAMETER Store
  Auto (default), SecretManagement or CredentialManager. Only used with -StoreKey.

.PARAMETER NoPrompt
  Never prompt interactively (stored-credential lookup still happens). Used by check_live_prereqs.ps1.

.PARAMETER EnvFile
  Env file path. Relative paths resolve against the repo root. Default: .env.live

.PARAMETER Run
  After a successful load, run `edu-it-generate` with the remaining arguments.
#>
[CmdletBinding()]
param(
    [string]$EnvFile = '.env.live',
    [switch]$Run,
    [string]$SecretName = 'edu-it-llm-provider-api-key',
    [switch]$StoreKey,
    [ValidateSet('Auto', 'SecretManagement', 'CredentialManager')]
    [string]$Store = 'Auto',
    [switch]$NoPrompt,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RunArgs
)

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:Dotted = ($MyInvocation.InvocationName -eq '.')

function script:Fail([string[]]$Problems) {
    Write-Host 'Live_Run environment has problems:' -ForegroundColor Red
    foreach ($p in $Problems) { Write-Host "  - $p" -ForegroundColor Red }
    $global:LASTEXITCODE = 1
}

if ($StoreKey) {
    & (Join-Path $PSScriptRoot 'live-run-credential.ps1') -StoreKey -SecretName $SecretName -Store $Store
    $code = $LASTEXITCODE
    if ($script:Dotted) { $global:LASTEXITCODE = $code; return } else { exit $code }
}

$problems = [System.Collections.Generic.List[string]]::new()
$loaded = [System.Collections.Generic.List[string]]::new()

# 1. Read the env file.
$path = if ([System.IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $script:RepoRoot $EnvFile }
if (Test-Path -LiteralPath $path -PathType Leaf) {
    $lineNo = 0
    foreach ($raw in Get-Content -LiteralPath $path) {
        $lineNo++
        $line = $raw.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { continue }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { $problems.Add("$EnvFile line ${lineNo}: expected KEY=VALUE"); continue }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if ($key -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { $problems.Add("$EnvFile line ${lineNo}: invalid variable name"); continue }
        if ($val.Length -ge 2 -and (($val[0] -eq '"' -and $val[-1] -eq '"') -or ($val[0] -eq "'" -and $val[-1] -eq "'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($key, $val, 'Process')
        $loaded.Add($key)
    }
    Write-Host "Loaded from ${EnvFile}: $($loaded -join ', ')"
} else {
    Write-Host "Env file not found ($EnvFile); using process environment and defaults only." -ForegroundColor Yellow
}

# 3. Non-secret defaults (ADR 0007 addendum), only when not already set.
$model = 'eu.anthropic.claude-sonnet-4-6'
$defaults = [ordered]@{
    LLM_PROVIDER_BACKEND       = 'live'
    LLM_PROVIDER_VENDOR        = 'bedrock_claude'
    LLM_PROVIDER_ENDPOINT      = 'https://bedrock-runtime.eu-central-1.amazonaws.com'
    LLM_PROVIDER_ALLOWED_HOSTS = 'bedrock-runtime.eu-central-1.amazonaws.com'
    LLM_MODEL_GENERATION       = $model
    LLM_MODEL_VERIFIER         = $model
    LLM_PRICES                 = "$model=300:1500"
}
$defaulted = @()
foreach ($k in $defaults.Keys) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($k, 'Process'))) {
        [Environment]::SetEnvironmentVariable($k, $defaults[$k], 'Process')
        $defaulted += $k
    }
}
if ($defaulted) { Write-Host "Defaults applied: $($defaulted -join ', ')" }

# 4. Credential: exactly one of key / secret id; placeholder counts as unset.
function script:Get-Set([string]$name) {
    $v = [Environment]::GetEnvironmentVariable($name, 'Process')
    if ([string]::IsNullOrWhiteSpace($v) -or $v.Trim() -eq 'change-me') { return $false }
    return $true
}
$hasKey = Get-Set 'LLM_PROVIDER_API_KEY'
$hasSecret = Get-Set 'LLM_PROVIDER_API_KEY_SECRET_ID'
if (-not $hasKey -and -not $hasSecret) {
    # Dot-sourcing re-binds the helper's own params (SecretName/Store) here, so keep ours.
    $wantedName = $SecretName
    . (Join-Path $PSScriptRoot 'live-run-credential.ps1')
    $stored = Get-LiveRunStoredKey -Name $wantedName
    if ($stored) {
        [Environment]::SetEnvironmentVariable('LLM_PROVIDER_API_KEY', $stored.Key, 'Process')
        $hasKey = $true
        Write-Host "LLM_PROVIDER_API_KEY loaded from $($stored.Source) entry '$wantedName' (value not shown)."
        $stored = $null
    }
}
if (-not $hasKey -and -not $hasSecret -and -not $NoPrompt -and [Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    $secure = Read-Host -Prompt 'LLM_PROVIDER_API_KEY (input hidden)' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if (-not [string]::IsNullOrWhiteSpace($plain) -and $plain.Trim() -ne 'change-me') {
        [Environment]::SetEnvironmentVariable('LLM_PROVIDER_API_KEY', $plain.Trim(), 'Process')
        $hasKey = $true
    }
    $plain = $null
}
if ($hasKey -and $hasSecret) {
    $problems.Add('both LLM_PROVIDER_API_KEY and LLM_PROVIDER_API_KEY_SECRET_ID are set; set exactly one')
} elseif (-not $hasKey -and -not $hasSecret) {
    $problems.Add('neither LLM_PROVIDER_API_KEY nor LLM_PROVIDER_API_KEY_SECRET_ID is set (placeholder change-me counts as unset)')
}

# global. model ids can route outside the EU (ADR 0007).
foreach ($k in 'LLM_MODEL_GENERATION', 'LLM_MODEL_VERIFIER') {
    $m = [Environment]::GetEnvironmentVariable($k, 'Process')
    if ($m -and $m.Trim().StartsWith('global.', [StringComparison]::OrdinalIgnoreCase)) {
        $problems.Add("$k uses a global. inference profile, which can route outside the EU; use eu.*")
    }
}
$prices = [Environment]::GetEnvironmentVariable('LLM_PRICES', 'Process')
if ($prices -and $prices -match '(^|[,;\s])global\.') {
    $problems.Add('LLM_PRICES mentions a global. model id')
}

# Endpoint: bare base URL whose host is in the allow-list.
$endpoint = [Environment]::GetEnvironmentVariable('LLM_PROVIDER_ENDPOINT', 'Process')
$uri = $null
if (-not [Uri]::TryCreate($endpoint, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https') {
    $problems.Add('LLM_PROVIDER_ENDPOINT must be an absolute https URL')
} else {
    if ($uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment) {
        $problems.Add('LLM_PROVIDER_ENDPOINT must be a bare base URL with no path (the adapter adds the Converse path)')
    }
    $allowed = ([Environment]::GetEnvironmentVariable('LLM_PROVIDER_ALLOWED_HOSTS', 'Process') -split '[,;\s]+' |
        Where-Object { $_ } | ForEach-Object { $_.ToLowerInvariant() })
    if ($allowed -notcontains $uri.Host.ToLowerInvariant()) {
        $problems.Add('the LLM_PROVIDER_ENDPOINT host is not in LLM_PROVIDER_ALLOWED_HOSTS')
    }
}

if ($problems.Count -gt 0) {
    Fail $problems
    if ($script:Dotted) { return } else { exit 1 }
}

$global:LASTEXITCODE = 0
Write-Host 'Live_Run environment OK (values not shown).' -ForegroundColor Green

if ($Run) {
    & edu-it-generate @RunArgs
    $code = $LASTEXITCODE
    if ($script:Dotted) { return } else { exit $code }
}
