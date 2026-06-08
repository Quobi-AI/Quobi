<#
.SYNOPSIS
  Build whisper.cpp with the GGML Vulkan backend on Windows and stage
  whisper-server.exe + its DLLs into winbundle/whisper/ for the Quobi installer.

.DESCRIPTION
  This is the Windows twin of the Linux whisper-vulkan build. It produces the
  same any-GPU / no-CUDA transcription server the Linux AppImage ships.

  A GPU is NOT required to BUILD: the Vulkan shaders compile to SPIR-V at build
  time via glslc (from the Vulkan SDK). A GPU is only needed to RUN/verify
  acceleration. On a machine with no Vulkan GPU the daemon falls back to
  faster-whisper (CPU) automatically.

.PREREQUISITES  (one-time, on the build VM)
  - Visual Studio 2022 Build Tools with the "Desktop development with C++" workload
  - CMake on PATH            (https://cmake.org/download/)
  - Vulkan SDK               (https://vulkan.lunarg.com/)  -> sets %VULKAN_SDK%
  - git

.USAGE
  From a "x64 Native Tools Command Prompt for VS 2022" (so cl/cmake see MSVC):
      powershell -ExecutionPolicy Bypass -File build-whisper-windows.ps1
  Optional:  -Ref <tag-or-commit>   (defaults to the same commit the Linux build used)
#>
param(
  [string]$Ref = "a8ec021"   # keep in lockstep with the Linux whisper-vulkan build
)

# NB: do NOT use ErrorActionPreference="Stop" here -- git/cmake write progress to
# stderr, which Stop turns into fatal NativeCommandErrors. We check the real
# success condition (whisper-server.exe exists) at the end instead.
$ErrorActionPreference = "Continue"

if (-not $env:VULKAN_SDK) {
  throw "VULKAN_SDK is not set. Install the Vulkan SDK from https://vulkan.lunarg.com/ and re-open the shell. (winget install KhronosGroup.VulkanSDK)"
}

# Resolve repo paths from this script's location:
#   <repo>/voice-type-desktop/src-tauri/scripts/build-whisper-windows.ps1
$srcTauri = Resolve-Path (Join-Path $PSScriptRoot "..")
$dest     = Join-Path $srcTauri "winbundle\whisper"
$work     = Join-Path $env:TEMP "quobi-whisper-build"

New-Item -ItemType Directory -Force -Path $work | Out-Null
Set-Location $work

# 1. Fetch whisper.cpp at the pinned ref.
if (-not (Test-Path "whisper.cpp")) {
  git clone https://github.com/ggml-org/whisper.cpp.git
}
Set-Location "whisper.cpp"
git fetch --all --tags
git checkout $Ref

# 2. Configure + build with Vulkan (Release). Explicit VS2022 x64 generator so
#    cmake finds MSVC via vswhere without needing a Native Tools prompt; the VS
#    generator is multi-config, so the build type is passed at --build time.
cmake -B build -G "Visual Studio 17 2022" -A x64 -DGGML_VULKAN=ON `
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON
cmake --build build --config Release -j

# 3. Locate the built artifacts (VS multi-config puts them under Release/).
$exe = Get-ChildItem -Path "build" -Recurse -Filter "whisper-server.exe" | Select-Object -First 1
if (-not $exe) { throw "whisper-server.exe not found under build/ -- did the build fail?" }
$binDir = $exe.Directory.FullName

# 4. Stage the server + every ggml/whisper DLL next to it (mirrors the Linux
#    co-located .so layout). vulkan-1.dll is a SYSTEM dll shipped by the GPU
#    driver -- do NOT bundle it.
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Get-ChildItem $dest -File | Remove-Item -Force
Copy-Item $exe.FullName $dest -Force
Get-ChildItem -Path $binDir -Filter "*.dll" | ForEach-Object { Copy-Item $_.FullName $dest -Force }
# ggml DLLs sometimes land in build/bin/Release vs build/ggml/... -- sweep both.
Get-ChildItem -Path "build" -Recurse -Filter "ggml*.dll"    | ForEach-Object { Copy-Item $_.FullName $dest -Force }
Get-ChildItem -Path "build" -Recurse -Filter "whisper*.dll" | ForEach-Object { Copy-Item $_.FullName $dest -Force }

Write-Host ""
Write-Host "Staged into $dest :" -ForegroundColor Green
Get-ChildItem $dest | Format-Table Name, Length -AutoSize

Write-Host "Next: build the Windows installer (bun run tauri build) -- winbundle/whisper/ is now bundled."
