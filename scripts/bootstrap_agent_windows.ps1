param(
    [Parameter(Mandatory = $true)]
    [string]$ServerUrl,

    [ValidateSet('full', 'build-only', 'flash-capture')]
    [string]$Mode = 'full',

    [ValidateSet('true', 'false', 'auto')]
    [string]$InstallBuildTools = 'auto',

    [string]$RepoUrl = 'https://github.com/koutrolikos/rtms.git',

    [string]$InstallDir = "$HOME\rtms-agent",

    [string]$OpenOcdTargetCfg = 'target/stm32g4x.cfg'
)

$ErrorActionPreference = 'Stop'

function Test-RequiredCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Show-MissingDependencyHelp {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Missing
    )

    $missingText = $Missing -join ', '
    Write-Error "Missing required tools: $missingText"
    Write-Host ''
    Write-Host 'Install missing tools manually and re-run this script.'
    Write-Host 'Suggested winget commands:'
    Write-Host '  winget install -e --id Python.Python.3.11'
    Write-Host '  winget install -e --id Git.Git'
    Write-Host '  winget install -e --id xpack-dev-tools.OpenOCD'
    Write-Host '  winget install -e --id Kitware.CMake'
    Write-Host 'Build tool note: install Arm GNU Toolchain if your firmware flow needs arm-none-eabi-gcc.'
}

function Install-MissingDependencies {
    param(
        [Parameter(Mandatory = $true)]
        [array]$MissingDeps
    )

    if (-not (Test-RequiredCommand -Name 'winget')) {
        Show-MissingDependencyHelp -Missing ($MissingDeps | ForEach-Object { $_.Name })
        throw 'winget is required to auto-install missing dependencies'
    }

    foreach ($dep in $MissingDeps) {
        Write-Host "Installing missing dependency: $($dep.Name)"
        winget install -e --id $dep.PackageId --accept-package-agreements --accept-source-agreements
    }
}

if ($InstallBuildTools -eq 'auto') {
    if ($Mode -eq 'full' -or $Mode -eq 'build-only') {
        $InstallBuildTools = 'true'
    }
    else {
        $InstallBuildTools = 'false'
    }
}

switch ($Mode) {
    'full' {
        $BuildCapable = 1
        $FlashCapable = 1
        $CaptureCapable = 1
    }
    'build-only' {
        $BuildCapable = 1
        $FlashCapable = 0
        $CaptureCapable = 0
    }
    'flash-capture' {
        $BuildCapable = 0
        $FlashCapable = 1
        $CaptureCapable = 1
    }
}

Write-Host '[1/4] Checking required tools'
$missing = @()
if (-not (Test-RequiredCommand -Name 'git')) {
    $missing += @{ Name = 'git'; PackageId = 'Git.Git' }
}
if (-not (Test-RequiredCommand -Name 'py')) {
    $missing += @{ Name = 'py (Python launcher)'; PackageId = 'Python.Python.3.11' }
}
if ($FlashCapable -eq 1 -or $CaptureCapable -eq 1) {
    if (-not (Test-RequiredCommand -Name 'openocd')) {
        $missing += @{ Name = 'openocd'; PackageId = 'xpack-dev-tools.OpenOCD' }
    }
}
if ($InstallBuildTools -eq 'true') {
    if (-not (Test-RequiredCommand -Name 'cmake')) {
        $missing += @{ Name = 'cmake'; PackageId = 'Kitware.CMake' }
    }
}

if ($missing.Count -gt 0) {
    Install-MissingDependencies -MissingDeps $missing
}

if (-not (Test-RequiredCommand -Name 'git')) {
    throw 'git is still missing after dependency installation'
}
if (-not (Test-RequiredCommand -Name 'py')) {
    throw 'Python launcher (py) is still missing after dependency installation'
}
if ($FlashCapable -eq 1 -or $CaptureCapable -eq 1) {
    if (-not (Test-RequiredCommand -Name 'openocd')) {
        throw 'openocd is still missing after dependency installation'
    }
}
if ($InstallBuildTools -eq 'true') {
    if (-not (Test-RequiredCommand -Name 'cmake')) {
        throw 'cmake is still missing after dependency installation'
    }
}

Write-Host '[2/4] Cloning or updating RTMS repo'
if (Test-Path "$InstallDir\.git") {
    git -C $InstallDir fetch --all --tags
    git -C $InstallDir pull --ff-only
}
else {
    git clone $RepoUrl $InstallDir
}

Write-Host '[3/4] Writing agent env file'
$envFile = "$InstallDir\.agent-env.ps1"
@"
`$env:RANGE_TEST_INSTALL_DIR = '$InstallDir'
`$env:RANGE_TEST_SERVER_URL = '$ServerUrl'
`$env:RANGE_TEST_AGENT_DATA_DIR = '$InstallDir\agent_data'
`$env:RANGE_TEST_SERVER_DATA_DIR = '$InstallDir\server_data'
`$env:RANGE_TEST_OPENOCD_TARGET_CFG = '$OpenOcdTargetCfg'
`$env:RANGE_TEST_AGENT_BUILD_CAPABLE = '$BuildCapable'
`$env:RANGE_TEST_AGENT_FLASH_CAPABLE = '$FlashCapable'
`$env:RANGE_TEST_AGENT_CAPTURE_CAPABLE = '$CaptureCapable'
`$env:RANGE_TEST_OPENOCD_SCAN_PROBES = '1'
`$env:RANGE_TEST_OPENOCD_SCAN_INTERVAL_SECONDS = '10'
`$env:RANGE_TEST_SIMULATE_HARDWARE = '0'
`$env:RANGE_TEST_SIMULATE_CAPTURE = '0'
`$env:RANGE_TEST_OPENOCD_RTT_SEARCH_ADDRESS = '0x20000000'
`$env:RANGE_TEST_OPENOCD_RTT_SEARCH_SIZE_BYTES = '131072'
`$env:RANGE_TEST_OPENOCD_RTT_ID = 'SEGGER RTT'
`$env:RANGE_TEST_OPENOCD_RTT_HUMAN_CHANNEL = '0'
`$env:RANGE_TEST_OPENOCD_RTT_MACHINE_CHANNEL = '1'
`$env:RANGE_TEST_OPENOCD_RTT_POLLING_INTERVAL_MS = '10'
`$env:RANGE_TEST_OPENOCD_RTT_STARTUP_TIMEOUT_SECONDS = '15'
"@ | Set-Content -Path $envFile -Encoding UTF8

Write-Host '[4/4] Basic connectivity check'
try {
    Invoke-WebRequest -Uri "$ServerUrl/healthz" -UseBasicParsing -TimeoutSec 5 | Out-Null
    Write-Host 'healthz: OK'
}
catch {
    Write-Warning "healthz check failed: $ServerUrl/healthz"
}

Write-Host ''
Write-Host 'Bootstrap complete.'
Write-Host 'Next commands:'
Write-Host "  cd $InstallDir"
Write-Host '  py -3.11 -m venv .venv'
Write-Host '  .\.venv\Scripts\Activate.ps1'
Write-Host '  python -m pip install --upgrade pip'
Write-Host '  pip install -e .'
Write-Host '  . .\.agent-env.ps1'
Write-Host '  .\.venv\Scripts\range-test-agent.exe run'
