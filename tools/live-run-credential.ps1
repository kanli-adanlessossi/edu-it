<#
.SYNOPSIS
  Looks up / stores the Live_Run provider API key in a local credential store (ai-content task 28.1).

.DESCRIPTION
  Used by tools/live-run-env.ps1 (which dot-sources this file for its functions). Lookup order:
    1. Microsoft.PowerShell.SecretManagement vault secret   (only if the module and a vault exist)
    2. Windows Credential Manager generic credential         (same target name; P/Invoke CredRead)
  Both use the name 'edu-it-llm-provider-api-key' unless -SecretName is given.

  The key is never written to a file, a profile, the command line or the output. Only the NAME of
  the store that supplied or received the key is ever printed. A missing module, vault or OS
  support is not an error: the store is simply skipped.

  Store a key (prompts with hidden input; run it directly, not dot-sourced):

      .\tools\live-run-credential.ps1 -StoreKey                          # first available store
      .\tools\live-run-credential.ps1 -StoreKey -Store CredentialManager
      .\tools\live-run-credential.ps1 -StoreKey -SecretName my-name

  Remove the stored entry from the chosen store(s):

      .\tools\live-run-credential.ps1 -RemoveKey

.PARAMETER StoreKey
  Prompt (Read-Host -AsSecureString) and store the key. Fails if the chosen store is unavailable.

.PARAMETER RemoveKey
  Delete the entry from the chosen store(s).

.PARAMETER Store
  Auto (default: SecretManagement if usable, else CredentialManager), SecretManagement, CredentialManager.

.PARAMETER SecretName
  Secret name / credential target. Default: edu-it-llm-provider-api-key
#>
[CmdletBinding()]
param(
    [switch]$StoreKey,
    [switch]$RemoveKey,
    [ValidateSet('Auto', 'SecretManagement', 'CredentialManager')]
    [string]$Store = 'Auto',
    [string]$SecretName = 'edu-it-llm-provider-api-key'
)

$script:DefaultSecretName = 'edu-it-llm-provider-api-key'

function ConvertTo-PlainText([securestring]$Secure) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

# ---- SecretManagement ------------------------------------------------------------------------
function Test-SecretManagementUsable {
    try {
        if (-not (Get-Module -ListAvailable -Name Microsoft.PowerShell.SecretManagement)) { return $false }
        Import-Module Microsoft.PowerShell.SecretManagement -ErrorAction Stop
        return [bool](Get-SecretVault -ErrorAction Stop)
    } catch { return $false }
}

function Get-SecretManagementKey([string]$Name) {
    if (-not (Test-SecretManagementUsable)) { return $null }
    try {
        $s = Get-Secret -Name $Name -ErrorAction Stop
        if ($s -is [securestring]) { return ConvertTo-PlainText $s }
        if ($s -is [string]) { return $s }
    } catch { }
    return $null
}

# ---- Windows Credential Manager (P/Invoke) ---------------------------------------------------
function Initialize-CredApi {
    if ($env:OS -ne 'Windows_NT') { return $false }
    if (-not ('EduItLiveRun.CredApi' -as [type])) {
        try {
            Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
namespace EduItLiveRun {
  public static class CredApi {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CREDENTIAL {
      public uint Flags; public uint Type; public string TargetName; public string Comment;
      public long LastWritten; public uint CredentialBlobSize; public IntPtr CredentialBlob;
      public uint Persist; public uint AttributeCount; public IntPtr Attributes;
      public string TargetAlias; public string UserName;
    }
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredRead(string target, uint type, uint flags, out IntPtr cred);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredWrite(ref CREDENTIAL cred, uint flags);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredDelete(string target, uint type, uint flags);
    [DllImport("advapi32.dll")]
    static extern void CredFree(IntPtr buffer);

    // Returns null when the entry does not exist.
    public static string Read(string target) {
      IntPtr p;
      if (!CredRead(target, 1, 0, out p)) return null;
      try {
        CREDENTIAL c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
        if (c.CredentialBlobSize == 0 || c.CredentialBlob == IntPtr.Zero) return null;
        return Marshal.PtrToStringUni(c.CredentialBlob, (int)(c.CredentialBlobSize / 2));
      } finally { CredFree(p); }
    }
    public static void Write(string target, string user, string secret) {
      byte[] blob = Encoding.Unicode.GetBytes(secret);
      IntPtr mem = Marshal.AllocHGlobal(blob.Length);
      try {
        Marshal.Copy(blob, 0, mem, blob.Length);
        CREDENTIAL c = new CREDENTIAL();
        c.Type = 1; c.TargetName = target; c.UserName = user;
        c.CredentialBlobSize = (uint)blob.Length; c.CredentialBlob = mem;
        c.Persist = 2; // CRED_PERSIST_LOCAL_MACHINE
        if (!CredWrite(ref c, 0)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
      } finally {
        for (int i = 0; i < blob.Length; i++) Marshal.WriteByte(mem, i, 0);
        Marshal.FreeHGlobal(mem);
        Array.Clear(blob, 0, blob.Length);
      }
    }
    public static bool Delete(string target) { return CredDelete(target, 1, 0); }
  }
}
'@
        } catch { return $false }
    }
    return $true
}

function Get-CredentialManagerKey([string]$Name) {
    if (-not (Initialize-CredApi)) { return $null }
    try { return [EduItLiveRun.CredApi]::Read($Name) } catch { return $null }
}

# ---- Public helpers --------------------------------------------------------------------------
# Returns @{ Key = <plain>; Source = 'SecretManagement'|'CredentialManager' } or $null.
function Get-LiveRunStoredKey([string]$Name = $script:DefaultSecretName) {
    $k = Get-SecretManagementKey $Name
    if (-not [string]::IsNullOrWhiteSpace($k)) { return @{ Key = $k.Trim(); Source = 'SecretManagement' } }
    $k = Get-CredentialManagerKey $Name
    if (-not [string]::IsNullOrWhiteSpace($k)) { return @{ Key = $k.Trim(); Source = 'CredentialManager' } }
    return $null
}

function Set-LiveRunStoredKey([securestring]$Secure, [string]$Name, [string]$Store = 'Auto') {
    if ($Store -in 'Auto', 'SecretManagement' -and (Test-SecretManagementUsable)) {
        Set-Secret -Name $Name -Secret $Secure -ErrorAction Stop
        return 'SecretManagement'
    }
    if ($Store -eq 'SecretManagement') { throw 'SecretManagement module or a registered vault is not available.' }
    if (-not (Initialize-CredApi)) { throw 'Windows Credential Manager is not available on this system.' }
    $plain = ConvertTo-PlainText $Secure
    try { [EduItLiveRun.CredApi]::Write($Name, 'edu-it', $plain.Trim()) } finally { $plain = $null }
    return 'CredentialManager'
}

function Remove-LiveRunStoredKey([string]$Name, [string]$Store = 'Auto') {
    $removed = @()
    if ($Store -in 'Auto', 'SecretManagement' -and (Test-SecretManagementUsable)) {
        try {
            if (Get-Secret -Name $Name -ErrorAction SilentlyContinue) {
                Remove-Secret -Name $Name -ErrorAction Stop; $removed += 'SecretManagement'
            }
        } catch { }
    }
    if ($Store -in 'Auto', 'CredentialManager' -and (Initialize-CredApi)) {
        if ([EduItLiveRun.CredApi]::Delete($Name)) { $removed += 'CredentialManager' }
    }
    return $removed
}

# ---- Direct invocation (not dot-sourced) -----------------------------------------------------
if ($MyInvocation.InvocationName -ne '.') {
    if ($StoreKey) {
        if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) {
            Write-Host 'Storing a key needs an interactive console (hidden prompt).' -ForegroundColor Red
            exit 1
        }
        $secure = Read-Host -Prompt "API key to store as '$SecretName' (input hidden)" -AsSecureString
        if ($secure.Length -eq 0) { Write-Host 'Empty input; nothing stored.' -ForegroundColor Red; exit 1 }
        try {
            $where = Set-LiveRunStoredKey -Secure $secure -Name $SecretName -Store $Store
            Write-Host "Stored '$SecretName' in $where (value not shown)." -ForegroundColor Green
        } catch {
            Write-Host "Could not store the key: $($_.Exception.Message)" -ForegroundColor Red
            exit 1
        } finally { $secure.Dispose() }
    } elseif ($RemoveKey) {
        $r = Remove-LiveRunStoredKey -Name $SecretName -Store $Store
        if ($r) { Write-Host "Removed '$SecretName' from: $($r -join ', ')" } else { Write-Host "No stored entry '$SecretName' found." }
    } else {
        Get-Help $MyInvocation.MyCommand.Path | Out-String | Write-Host
    }
}
