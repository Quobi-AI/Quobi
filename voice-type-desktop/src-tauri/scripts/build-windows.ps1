<#
.SYNOPSIS
  One-command Quobi Windows build: daemon (PyInstaller) + GUI (Tauri/Rust),
  INCREMENTAL so repeat builds are fast. Optionally swaps the fresh binaries
  into the installed app (with .bak backups), or produces the NSIS installer.

.DESCRIPTION
  Why the first build feels slow -- and why this script makes the rest fast:
    * GUI: cargo compiles every Rust crate the FIRST time (~several minutes).
      Reusing src-tauri/target/ makes later builds incremental (just your code).
    * Daemon: pip installs the stack (sherpa-onnx) the FIRST time. This script
      reuses the .venv and ONLY reinstalls when requirements.txt changes (sha
      marker), so later builds skip straight to PyInstaller (~1-2 min).
  Net: first run ~15-20 min cold; every run after that is a couple minutes.

  NOTE: this file is intentionally pure ASCII -- Windows PowerShell 5.1 reads
  .ps1 as Windows-1252 (not UTF-8) when there's no BOM, so any fancy Unicode
  (checkmarks, em-dashes, ellipses) corrupts parsing. Keep it ASCII.

.PARAMETER Src        Repo root on this machine. Default C:\quobi-src.
.PARAMETER Component  daemon | gui | both   (default both)
.PARAMETER Install    Swap the built exes into the installed Quobi app (.bak first).
.PARAMETER Installer  Run `tauri build` (NSIS installer) instead of a plain cargo
                      build for the GUI. Needs winbundle/ populated (see BUILD.md).

.EXAMPLE
  # rebuild everything and hot-swap it into the running install
  powershell -ExecutionPolicy Bypass -File build-windows.ps1 -Install

.EXAMPLE
  # just the GUI (after a Rust-side change), swapped in
  powershell -ExecutionPolicy Bypass -File build-windows.ps1 -Component gui -Install

.NOTES
  Prereqs (one-time, see BUILD.md section 4): VS 2022 BuildTools (C++), Rust,
  Bun, Python 3.12, and the VC++ redist. Run from any shell -- cargo finds MSVC
  via the VS toolchain; no "Native Tools" prompt required.
  Do NOT set $ErrorActionPreference='Stop' here -- git/cargo/cmake/pip write
  progress to stderr, which Stop turns into fatal errors.
#>
param(
  [string]$Src = "C:\quobi-src",
  [ValidateSet("daemon", "gui", "both")] [string]$Component = "both",
  [switch]$Install,
  [switch]$Installer
)
$ErrorActionPreference = "Continue"
function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Fail($m) { Write-Host "FAILED: $m" -ForegroundColor Red; exit 1 }

$desktop = Join-Path $Src "voice-type-desktop"
$engine  = Join-Path $Src "voice-type"
if (-not (Test-Path $desktop)) { Fail "no voice-type-desktop under $Src" }

$daemonExe = $null
$guiExe = $null

# --- Daemon (PyInstaller) ----------------------------------------------------
if ($Component -in @("daemon", "both")) {
  Step "Daemon (PyInstaller) - incremental venv"
  Push-Location $engine
  try {
    $venvPy = ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
      Write-Host "creating venv..."
      py -m venv .venv
      & $venvPy -m pip install -U pip | Out-Null
    }
    # Reinstall deps ONLY when requirements.txt changed (sha marker) -- this is
    # the slow step we skip on warm builds.
    $reqHash = (Get-FileHash requirements.txt -Algorithm SHA256).Hash
    $marker  = ".venv\.reqhash"
    if (-not (Test-Path $marker) -or ((Get-Content $marker -Raw).Trim() -ne $reqHash)) {
      Write-Host "requirements changed (or first run) - installing deps..."
      & ".venv\Scripts\pip.exe" install -r requirements.txt pyinstaller
      if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
      Set-Content $marker $reqHash
    } else {
      Write-Host "deps unchanged - skipping pip install."
    }
    & ".venv\Scripts\pyinstaller.exe" --clean --noconfirm voice-type.spec
    $daemonExe = Join-Path $engine "dist\voice-type.exe"
    if (-not (Test-Path $daemonExe)) { Fail "daemon build produced no dist\voice-type.exe" }
    Write-Host ("[ok] daemon: {0} ({1} bytes)" -f $daemonExe, (Get-Item $daemonExe).Length)
  } finally { Pop-Location }
}

# --- GUI (Tauri / Rust) ------------------------------------------------------
if ($Component -in @("gui", "both")) {
  Step "GUI (Tauri / Rust) - incremental cargo"
  Push-Location $desktop
  try {
    # Tauri's build script validates the winbundle/* resource globs from
    # tauri.windows.conf.json even for a plain `cargo build`, and FAILS if a glob
    # matches nothing. winbundle/ is gitignored (built artifacts), so it may be
    # empty here. For a bare exe build the resources aren't embedded anyway, so
    # satisfy the globs with stubs when empty. A real -Installer build needs the
    # actual shipping binaries staged (see BUILD.md section 4) -- fail loudly.
    $wb = "src-tauri\winbundle"
    New-Item -ItemType Directory -Force -Path (Join-Path $wb "daemon"), (Join-Path $wb "llama") | Out-Null
    $daemonRes = Join-Path $wb "daemon\voice-type.exe"
    if ($daemonExe -and (Test-Path $daemonExe)) {
      # We built the daemon this run -- ALWAYS stage the fresh copy, overwriting
      # any stale stub. (The old "only if missing" logic silently shipped a stub
      # left behind by a prior bare -Component gui build.)
      Copy-Item $daemonExe $daemonRes -Force
    } elseif (-not (Test-Path $daemonRes)) {
      if ($Installer) { Fail "winbundle\daemon\voice-type.exe missing - an installer needs the real daemon (run with -Component both first)" }
      Set-Content $daemonRes "stub - not embedded; bare cargo build only"
    }
    # Never let a stub daemon (the bare-cargo placeholder) reach an installer.
    if ($Installer -and (Get-Item $daemonRes).Length -lt 1MB) {
      Fail "winbundle\daemon\voice-type.exe is a stub ($((Get-Item $daemonRes).Length) bytes) - rebuild the daemon with -Component both"
    }
    foreach ($sub in @("llama")) {
      $p = Join-Path $wb $sub
      $real = Get-ChildItem $p -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne ".stub" }
      if (-not $real) {
        if ($Installer) { Fail "winbundle\$sub is empty - an installer needs the real Vulkan binaries (see BUILD.md section 4)" }
        Set-Content (Join-Path $p ".stub") "placeholder to satisfy the resource glob; not embedded in a bare exe build"
      }
    }

    if (-not (Test-Path "node_modules")) { Write-Host "bun install..."; bun install }
    # IMPORTANT: build the GUI with `tauri build`, NOT a bare `cargo build`. A
    # plain cargo build makes Tauri load the UI from the dev server
    # (devUrl = http://localhost:1420), so the installed app shows
    # "localhost refused to connect". `tauri build` embeds the frontend
    # (frontendDist) for a real production binary. --no-bundle skips the slow
    # NSIS packaging when we only need the exe to hot-swap.
    if ($Installer) {
      bun run tauri build               # full NSIS installer
      if ($LASTEXITCODE -ne 0) { Fail "tauri build failed" }
    } else {
      bun run tauri build --no-bundle   # production exe, no installer
      if ($LASTEXITCODE -ne 0) { Fail "tauri build --no-bundle failed" }
    }
    # tauri build names the exe quobi.exe per mainBinaryName; older bare cargo
    # builds produced voice-type-desktop.exe. Match either.
    $guiExe = Get-ChildItem "src-tauri\target\release" -Filter "*.exe" -ErrorAction SilentlyContinue |
              Where-Object { $_.Name -match "^(quobi|voice-type-desktop)\.exe$" } |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $guiExe) { Fail "GUI build produced no exe under target\release" }
    Write-Host ("[ok] gui: {0} ({1} bytes)" -f $guiExe.FullName, $guiExe.Length)
  } finally { Pop-Location }
}

# --- Install: hot-swap into the running app (with backups) -------------------
if ($Install) {
  Step "Install - swap into the installed app (.bak backups)"
  $appDirs = @("$env:LOCALAPPDATA\Quobi", "$env:LOCALAPPDATA\Programs\Quobi") |
             Where-Object { Test-Path $_ }
  if (-not $appDirs) { Write-Host "no installed Quobi found under %LOCALAPPDATA% - nothing to swap." }

  # Stop running instances first: Windows locks a running .exe, so an overwrite
  # (or rcedit) silently fails otherwise. Always stop the daemon + sidecars;
  # stop the GUI too only when we're replacing it. We relaunch at the end.
  $guiWasUp = $null
  $toStop = @("voice-type", "llama-server")
  if ($guiExe) {
    $guiWasUp = Get-Process voice-type-desktop, quobi -ErrorAction SilentlyContinue | Select-Object -First 1
    $toStop += @("voice-type-desktop", "quobi")
  }
  Get-Process $toStop -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep -Seconds 2

  foreach ($d in $appDirs) {
    if ($daemonExe) {
      # daemon can live in the app root AND a daemon\ subdir - swap every copy.
      Get-ChildItem $d -Recurse -Filter "voice-type.exe" -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName "$($_.FullName).bak" -Force
        Copy-Item $daemonExe $_.FullName -Force
        Write-Host "  daemon -> $($_.FullName)"
      }
    }
    if ($guiExe) {
      Get-ChildItem $d -Filter "*.exe" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "^(quobi|voice-type-desktop)\.exe$" } | ForEach-Object {
          Copy-Item $_.FullName "$($_.FullName).bak" -Force
          Copy-Item $guiExe.FullName $_.FullName -Force   # content swap; installed filename kept
          Write-Host "  gui    -> $($_.FullName)"
        }
    }
  }
  # Brand the prebuilt cleanup sidecar (llama-server) so Task Manager shows
  # "Quobi Cleanup Engine", not the raw filename. (Our daemon gets its
  # version resource from voice-type.spec; the GUI is branded by Tauri.) Now
  # that the sidecars are stopped, their exes are unlocked for rcedit.
  & (Join-Path $PSScriptRoot "brand-windows-binaries.ps1")

  # Relaunch so the app comes back up. If we replaced the GUI (and it was up),
  # start it -- it spawns the daemon. Otherwise start the daemon directly.
  $guiInstalled = Get-ChildItem $appDirs -Filter "*.exe" -ErrorAction SilentlyContinue |
                  Where-Object { $_.Name -match "^(quobi|voice-type-desktop)\.exe$" } | Select-Object -First 1
  if ($guiExe -and $guiWasUp -and $guiInstalled) {
    Start-Process $guiInstalled.FullName
    Write-Host "relaunched the GUI."
  } else {
    $d = Get-ChildItem $appDirs -Recurse -Filter "voice-type.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($d) { Start-Process $d.FullName -ArgumentList "--daemon"; Write-Host "relaunched the daemon." }
    else { Write-Host "Restart Quobi to pick up the swapped binaries." }
  }
}

Step "Done"
