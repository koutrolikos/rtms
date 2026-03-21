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

Write-Host '[1/5] Installing required tools via winget'
winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
winget install -e --id xpack-dev-tools.OpenOCD --accept-package-agreements --accept-source-agreements

if ($InstallBuildTools -eq 'true') {
    winget install -e --id Kitware.CMake --accept-package-agreements --accept-source-agreements
    Write-Host 'Build tools note: install ARM GCC toolchain if your firmware recipe needs it.'
    Write-Host 'Example: Arm GNU Toolchain from Arm (arm-none-eabi-gcc)'
}

Write-Host '[2/5] Cloning or updating RTMS repo'
if (Test-Path "$InstallDir\.git") {
    git -C $InstallDir fetch --all --tags
    git -C $InstallDir pull --ff-only
}
else {
    git clone $RepoUrl $InstallDir
}

Write-Host '[3/5] Creating virtualenv and installing package'
py -3.11 -m venv "$InstallDir\.venv"
& "$InstallDir\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$InstallDir\.venv\Scripts\python.exe" -m pip install -e $InstallDir

Write-Host '[4/5] Writing agent env file'
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
"@ | Set-Content -Path $envFile -Encoding UTF8

Write-Host '[5/5] Basic connectivity check'
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
Write-Host '  .\.venv\Scripts\Activate.ps1'
Write-Host '  . .\.agent-env.ps1'
Write-Host '  range-test-agent run'
