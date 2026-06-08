<#
.SYNOPSIS
  Brand the prebuilt sidecar exes so Windows Task Manager / file Properties show
  "Quobi ..." instead of the raw filename ("llama-server.exe"). Sets the PE
  FileDescription/ProductName in place with rcedit.

.DESCRIPTION
  Only touches the THIRD-PARTY sidecars (llama-server.exe, whisper-server.exe) --
  plain C++ exes, safe to rcedit. Our own daemon (voice-type.exe) gets its
  version resource at BUILD time via voice-type.spec (don't rcedit a PyInstaller
  onefile), and the GUI (Quobi.exe) is branded by Tauri. Idempotent: re-running
  just re-stamps the same strings.

  Pure ASCII on purpose (Windows PowerShell 5.1 reads .ps1 as Windows-1252).

.PARAMETER Root  Directory to scan recursively for the sidecar exes. Default:
                 the installed Quobi app under %LOCALAPPDATA%.
#>
param([string]$Root = "")
$ErrorActionPreference = "Continue"

# filename -> FileDescription shown in Task Manager
$brand = [ordered]@{
  "llama-server.exe"   = "Quobi Cleanup Engine"
  "whisper-server.exe" = "Quobi Speech Engine"
}

# Fetch rcedit once, cache it.
$rcedit = Join-Path $env:LOCALAPPDATA "quobi-build\rcedit-x64.exe"
if (-not (Test-Path $rcedit)) {
  New-Item -ItemType Directory -Force -Path (Split-Path $rcedit) | Out-Null
  $url = "https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe"
  Write-Host "downloading rcedit -> $rcedit"
  try { Invoke-WebRequest -Uri $url -OutFile $rcedit -UseBasicParsing }
  catch { Write-Host "FAILED to download rcedit: $_" -ForegroundColor Red; exit 1 }
}

$roots = if ($Root) { @($Root) } else {
  @("$env:LOCALAPPDATA\Quobi", "$env:LOCALAPPDATA\Programs\Quobi") | Where-Object { Test-Path $_ }
}
if (-not $roots) { Write-Host "no install/stage dir to brand (pass -Root)"; exit 0 }

$n = 0
foreach ($r in $roots) {
  foreach ($name in $brand.Keys) {
    Get-ChildItem $r -Recurse -Filter $name -ErrorAction SilentlyContinue | ForEach-Object {
      # NB: --set-file-version/--set-product-version FIRST so rcedit CREATES the
      # VS_VERSION_INFO block (these sidecars ship with none) before the strings
      # land. Without it rcedit exits 0 but writes nothing.
      & $rcedit $_.FullName `
        --set-file-version "0.1.0.0" --set-product-version "0.1.0.0" `
        --set-version-string "FileDescription" $brand[$name] `
        --set-version-string "ProductName" "Quobi" `
        --set-version-string "CompanyName" "Quobi"
      if ($LASTEXITCODE -ne 0) { Write-Host ("  FAILED rcedit {0} (is it running/locked?)" -f $_.FullName) -ForegroundColor Red; return }
      # Read back via rcedit (authoritative; Get-Item .VersionInfo caches per path).
      $desc = (& $rcedit $_.FullName --get-version-string "FileDescription")
      Write-Host ("  {0} -> '{1}'" -f $_.FullName, $desc)
      $n++
    }
  }
}
Write-Host "branded $n sidecar binary(ies)."
