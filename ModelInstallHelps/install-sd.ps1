# Installs stable-diffusion.cpp (sd-cli and sd-server) for Axis on Windows.
#
#   powershell -ExecutionPolicy Bypass -File install-sd.ps1                 pick the build for this PC
#   powershell -ExecutionPolicy Bypass -File install-sd.ps1 -Build cuda     NVIDIA GPU (fastest on NVIDIA)
#   powershell -ExecutionPolicy Bypass -File install-sd.ps1 -Build vulkan   any modern GPU (NVIDIA, AMD, Intel)
#   powershell -ExecutionPolicy Bypass -File install-sd.ps1 -Build rocm     AMD GPU with ROCm
#   powershell -ExecutionPolicy Bypass -File install-sd.ps1 -Build cpu      no GPU
#
# Installs to %USERPROFILE%\sd (change with -Dest) and points Axis at it with AXIS_SD_CLI.
param(
    [ValidateSet("auto", "cuda", "vulkan", "rocm", "cpu")][string]$Build = "auto",
    [string]$Dest = (Join-Path $HOME "sd")
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # the progress bar makes downloads very slow in Windows PowerShell 5
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$Repo = "leejet/stable-diffusion.cpp"

function Fail([string]$Message) {
    Write-Host "Error: $Message" -ForegroundColor Red
    exit 1
}

if ($env:OS -ne "Windows_NT") { Fail "This script is for Windows. On macOS or Linux use install-sd.sh." }
if (-not [Environment]::Is64BitOperatingSystem) { Fail "stable-diffusion.cpp needs 64-bit Windows." }

# --- choose the build -------------------------------------------------------
if ($Build -eq "auto") {
    $gpus = @()
    try { $gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) } catch { }
    $hasNvidiaSmi = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
    if ($hasNvidiaSmi -or ($gpus -match "NVIDIA")) {
        $Build = "cuda"
    } elseif ($gpus | Where-Object { $_ -notmatch "Basic Display|Remote Display|Hyper-V|Virtual" }) {
        $Build = "vulkan"
    } else {
        $Build = "cpu"
    }
    if ($gpus) { Write-Host "Graphics: $($gpus -join ', ')" }
}
$pattern = @{
    cuda   = "-bin-win-cuda12-x64\.zip$"
    vulkan = "-bin-win-vulkan-x64\.zip$"
    rocm   = "-bin-win-rocm-.*-x64\.zip$"
    cpu    = "-bin-win-cpu-x64\.zip$"
}[$Build]
Write-Host "Build: $Build"

# --- find the latest release -----------------------------------------------
Write-Host "Looking up the latest stable-diffusion.cpp release..."
try {
    $req = [System.Net.WebRequest]::Create("https://github.com/$Repo/releases/latest")
    $req.AllowAutoRedirect = $false
    $resp = $req.GetResponse()
    $location = $resp.Headers["Location"]
    $resp.Close()
} catch {
    Fail "Could not reach github.com. Check your internet connection. ($($_.Exception.Message))"
}
$tag = ($location -split "/")[-1]
if (-not $tag -or $tag -eq "latest") { Fail "Could not find the latest release." }
try {
    $page = (Invoke-WebRequest -UseBasicParsing "https://github.com/$Repo/releases/expanded_assets/$tag").Content
} catch {
    Fail "Could not list the release files. ($($_.Exception.Message))"
}
$assets = @([regex]::Matches($page, "/$Repo/releases/download/[^`"]+?\.zip") | ForEach-Object { $_.Value } | Sort-Object -Unique)
$asset = $assets | Where-Object { $_ -match $pattern } | Select-Object -First 1
if (-not $asset) {
    Fail ("Release $tag has no '$Build' build for Windows. Files available:`n" + (($assets | ForEach-Object { "  " + ($_ -split "/")[-1] }) -join "`n"))
}
$downloads = @($asset)
if ($Build -eq "cuda") {
    $cudart = $assets | Where-Object { $_ -match "cudart-.*-win-cu12-x64\.zip$" } | Select-Object -First 1
    if (-not $cudart) { Fail "Release $tag is missing the CUDA runtime file. Run again with -Build vulkan." }
    $downloads += $cudart
}
Write-Host "Release $tag"

# --- download and unpack ---------------------------------------------------
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("sd-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    foreach ($d in $downloads) {
        $name = ($d -split "/")[-1]
        $zip = Join-Path $tmp $name
        Write-Host "Downloading $name ..."
        $ok = $false
        foreach ($try in 1..3) {
            try { Invoke-WebRequest -UseBasicParsing -Uri ("https://github.com" + $d) -OutFile $zip; $ok = $true; break }
            catch { Write-Host "  Download failed ($($_.Exception.Message)); retrying..."; Start-Sleep -Seconds (2 * $try) }
        }
        if (-not $ok) { Fail "The download of $name failed. Run this again." }
        $out = Join-Path $tmp ([IO.Path]::GetFileNameWithoutExtension($name))
        try { Expand-Archive -Path $zip -DestinationPath $out -Force }
        catch { Fail "The download of $name is damaged. Run this again." }
    }

    $cli = Get-ChildItem -Path $tmp -Recurse -Filter "sd-cli.exe" | Select-Object -First 1
    if (-not $cli) { Fail "sd-cli.exe is not in the downloaded archive." }
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    try {
        Copy-Item -Path (Join-Path $cli.DirectoryName "*") -Destination $Dest -Recurse -Force
        # The CUDA runtime DLLs must sit next to sd-cli.exe.
        Get-ChildItem -Path $tmp -Recurse -Filter "*.dll" |
            Where-Object { $_.Name -match "^(cudart|cublas|cublasLt)64_" } |
            Copy-Item -Destination $Dest -Force
    } catch {
        Fail "Could not write to $Dest. If Axis is running, stop it (Ctrl+C in its window) and run this again. ($($_.Exception.Message))"
    }
    Get-ChildItem -Path $Dest -Recurse | Unblock-File -ErrorAction SilentlyContinue
} finally {
    Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# --- check it runs ---------------------------------------------------------
$exe = Join-Path $Dest "sd-cli.exe"
$ErrorActionPreference = "Continue"   # Windows PowerShell 5 would stop on any text sd-cli writes to stderr
$version = & $exe --version 2>&1 | Out-String
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) {
    Write-Host $version
    if ($LASTEXITCODE -eq -1073741515) {   # 0xC0000135: a DLL is missing
        Fail "sd-cli.exe needs the Microsoft Visual C++ runtime. Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe and run this again."
    }
    Fail "sd-cli.exe was installed in $Dest but does not run (exit code $LASTEXITCODE)."
}
Write-Host ("Installed: " + $version.Trim())
if (Test-Path (Join-Path $Dest "sd-server.exe")) {
    Write-Host "sd-server is included, so Axis can keep image models on."
} else {
    Write-Host "Note: this release has no sd-server; Axis will use sd-cli for each picture."
}
if ($Build -eq "cuda") {
    Write-Host "CUDA build: if pictures fail to start, update the NVIDIA driver, or run this again with -Build vulkan."
}

# --- point Axis at it ------------------------------------------------------
[Environment]::SetEnvironmentVariable("AXIS_SD_CLI", $exe, "User")
Write-Host ""
Write-Host "Done. Close this window, open a new PowerShell, and run axis."
Write-Host "Then on the Models page, choose Image model under Install a model."
