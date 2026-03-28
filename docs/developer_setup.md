# Developer Setup

## Start Here (First-Time Setup)

Do these first, in this order:

1. Clone the repo.
2. Enter the repo folder.
3. Run one bootstrap script for your OS.

```bash
git clone https://github.com/koutrolikos/rtms.git
cd rtms
```

macOS:

```bash
./scripts/bootstrap_host_macos.sh --server-url http://172.20.10.3:8000
```

Linux:

```bash
./scripts/bootstrap_host_linux.sh --server-url http://172.20.10.3:8000
```

Windows PowerShell:

```powershell
.\scripts\bootstrap_host_windows.ps1 -ServerUrl http://172.20.10.3:8000
```

After bootstrap, source the generated env file and run binaries from the venv:

```bash
source ~/rtms-host/.rtms-env.sh
~/rtms-host/.venv/bin/rtms-server run
~/rtms-host/.venv/bin/rtms-host run
```

On Windows PowerShell:

```powershell
. ~/rtms-host/.rtms-env.ps1
~/rtms-host/.venv/Scripts/rtms-server.exe run
~/rtms-host/.venv/Scripts/rtms-host.exe run
```

## Fastest Path (Fresh Machine)

If your goal is "start a new host on a random machine with minimal effort", use one of these exact flows.

### Script-first path (recommended)

Linux:

```bash
./scripts/bootstrap_host_linux.sh --server-url http://172.20.10.3:8000
```

macOS:

```bash
./scripts/bootstrap_host_macos.sh --server-url http://172.20.10.3:8000
```

Windows PowerShell:

```powershell
.\scripts\bootstrap_host_windows.ps1 -ServerUrl http://172.20.10.3:8000
```

Modes supported by both scripts:

- `full` (default): build + flash + capture
- `build-only`
- `flash-capture`

Note: bootstrap scripts install missing dependencies automatically, then clone/update RTMS and write environment files.

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl openocd make cmake gcc-arm-none-eabi

git clone https://github.com/koutrolikos/rtms.git
cd rtms
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

export RTMS_SERVER_URL="http://172.20.10.3:8000"
export RTMS_OPENOCD_TARGET_CFG="target/stm32g4x.cfg"
rtms-host run
```

### Windows (PowerShell)

```powershell
winget install -e --id Python.Python.3.11
winget install -e --id Git.Git
winget install -e --id xpack-dev-tools.OpenOCD

git clone https://github.com/koutrolikos/rtms.git
cd rtms
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

$env:RTMS_SERVER_URL = "http://172.20.10.3:8000"
$env:RTMS_OPENOCD_TARGET_CFG = "target/stm32g4x.cfg"
rtms-host run
```

If OpenOCD is not on PATH:

```powershell
$env:RTMS_OPENOCD_BIN = "C:\openocd\xpack-openocd-0.12.0-7\bin\openocd.exe"
$env:OPENOCD_SCRIPTS = "C:\openocd\xpack-openocd-0.12.0-7\openocd\scripts"
```

### One sanity check before `run`

From the host host:

```bash
curl http://172.20.10.3:8000/healthz
```

If server auth is enabled, keep using `/healthz` for the anonymous liveness check, then export:

```bash
export RTMS_SERVER_USERNAME="rtms"
export RTMS_SERVER_PASSWORD="change-me"
```

## Dependency Matrix (By Host Capability)

Install only what the host will do.

- Core host (always): Python 3.11+, pip/venv, server network access
- Build-capable: `git` + build tools required by the repo recipe
- High-Altitude-CC build recipe today: `cmake`, `arm-none-eabi-gcc`
- Flash-capable: `openocd` + correct interface/target configs
- Capture-capable: built-in OpenOCD RTT capture shipped with the host

Built-in capture defaults:

- search for RTT control block ID `SEGGER RTT` from `0x20000000` for `131072` bytes
- capture human RTT channel `0` into `rtt.log`
- capture machine `MLOG` RTT channel `1` into `rtt.rttbin`
- capture OpenOCD stdout/stderr into `capture-command.log`

`RTMS_CAPTURE_COMMAND_TEMPLATE` remains available as an override for custom capture tools.

Capability flags:

```bash
export RTMS_HOST_BUILD_CAPABLE=1
export RTMS_HOST_FLASH_CAPABLE=1
export RTMS_HOST_CAPTURE_CAPABLE=1
```

Build-only host:

```bash
export RTMS_HOST_BUILD_CAPABLE=1
export RTMS_HOST_FLASH_CAPABLE=0
export RTMS_HOST_CAPTURE_CAPABLE=0
```

Flash/capture-only host:

```bash
export RTMS_HOST_BUILD_CAPABLE=0
export RTMS_HOST_FLASH_CAPABLE=1
export RTMS_HOST_CAPTURE_CAPABLE=1
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Configure Repos

The server reads repo definitions from `server_data/repos.json` by default.

This build capability is recipe-driven, not generic source compilation. Each
repo entry must describe a build that works from a clean clone on the build
host. If a firmware repo depends on generated IDE outputs, untracked makefiles,
local toolchain wrappers, or other files that are not present in Git, the host
build will fail and you should use prebuilt ELF upload instead.

Example:

```json
[
  {
    "id": "rf-fw",
    "display_name": "RF Firmware",
    "full_name": "your-org/private-rf-fw",
    "clone_url": "git@github.com:your-org/private-rf-fw.git",
    "api_url": "https://api.github.com",
    "default_branch": "main",
    "build_recipe": {
      "build_command": "make -C firmware release",
      "artifact_globs": [
        "firmware/build/**/*.elf",
        "firmware/build/**/*.bin",
        "firmware/build/**/*.hex"
      ],
      "elf_glob": "firmware/build/**/*.elf",
      "flash_image_glob": "firmware/build/**/*.elf",
      "checkout_subdir": ".",
      "timeout_seconds": 1200,
      "env": {},
      "rtt_symbol": "_SEGGER_RTT"
    }
  }
]
```

The bundled `High-Altitude-CC` example is configured as a single repo entry
built through:

```bash
rtms-host build-high-altitude-cc --source . --build-dir build/debug --role <tx|rx> --build-config-json <json>
```

RTMS resolves the operator-selected TX/RX role plus the exposed firmware build
config into a generated High-Altitude-CC build config JSON payload, patches the
clean clone's `Core/Inc/app_config.h`, runs the tracked CMake build, uploads the
build log as a raw artifact, and removes the host's local build workspace after
a successful upload.

## Run Server

```bash
rtms-server
```

By default the server binds to `0.0.0.0:8000` and auto-detects a LAN URL for host-facing links.
If the detected address is wrong, set:

```bash
export RTMS_SERVER_PUBLIC_BASE_URL="http://172.20.10.3:8000"
```

Optional HTTP Basic auth:

```bash
export RTMS_AUTH_USERNAME="rtms"
export RTMS_AUTH_PASSWORD="change-me"
```

When enabled, hosts must also set matching `RTMS_SERVER_USERNAME` and
`RTMS_SERVER_PASSWORD` values.

When the machine was bootstrapped with the provided scripts, runtime state is pinned under
the install directory by default:

- `RTMS_HOST_DATA_DIR=<install-dir>/host_data`
- `RTMS_SERVER_DATA_DIR=<install-dir>/server_data`

That avoids creating `host_data/` or `server_data/` in whichever directory the user happened
to be in when launching `rtms-host` or `rtms-server`.

Key env vars:

- `RTMS_SERVER_HOST`
- `RTMS_SERVER_PORT`
- `RTMS_SERVER_PUBLIC_BASE_URL`
- `RTMS_SERVER_DATA_DIR`
- `RTMS_SERVER_DB_URL`
- `RTMS_REPO_CONFIG`
- `GITHUB_TOKEN`

## Run Host

```bash
export RTMS_SERVER_URL="http://172.20.10.3:8000"
rtms-host run
```

If the host is running on the same machine as the server for local
development, use:

```bash
export RTMS_SERVER_URL="http://127.0.0.1:8000"
```

From Windows PowerShell:

```powershell
$env:RTMS_SERVER_URL = "http://172.20.10.3:8000"
$env:RTMS_OPENOCD_TARGET_CFG = "target/stm32g4x.cfg"
rtms-host run
```

Verify connectivity from the host machine before starting the host:

```bash
curl http://172.20.10.3:8000/healthz
```

Key env vars:

- `RTMS_SERVER_URL`
- `RTMS_HOST_NAME`
- `RTMS_HOST_LABEL`
- `RTMS_HOST_BUILD_CAPABLE`
- `RTMS_SIMULATE_HARDWARE`
- `RTMS_SIMULATE_CAPTURE`
- `RTMS_OPENOCD_BIN`
- `RTMS_OPENOCD_INTERFACE_CFG`
- `RTMS_OPENOCD_TARGET_CFG`
- `RTMS_OPENOCD_SCAN_PROBES`
- `RTMS_OPENOCD_RTT_SEARCH_ADDRESS`
- `RTMS_OPENOCD_RTT_SEARCH_SIZE_BYTES`
- `RTMS_OPENOCD_RTT_ID`
- `RTMS_OPENOCD_RTT_HUMAN_CHANNEL`
- `RTMS_OPENOCD_RTT_MACHINE_CHANNEL`
- `RTMS_CAPTURE_COMMAND_TEMPLATE`

Simulation defaults to OFF:

- `RTMS_SIMULATE_HARDWARE=0` unless explicitly set to `1`
- `RTMS_SIMULATE_CAPTURE=0` unless explicitly set to `1`

By default the host scans for connected probes, auto-selects one probe when
exactly one is present, and uses the built-in OpenOCD RTT capturer. Use
`rtms-host probe-scan` to inspect what the host sees before running a
session.

`RTMS_CAPTURE_COMMAND_TEMPLATE` is optional. If you set it, it should
format against these placeholders:

- `{role}`
- `{session_id}`
- `{role_run_id}`
- `{probe_serial}`
- `{elf_path}`
- `{duration_seconds}`
- `{rtt_human_log_path}`
- `{rtt_machine_log_path}`
- `{rtt_log_path}` for legacy human-log capture scripts
- `{capture_command_log_path}`

For STM32G474-based boards, the correct default OpenOCD target script is:

```bash
export RTMS_OPENOCD_TARGET_CFG="target/stm32g4x.cfg"
```

If OpenOCD is installed on Windows but not on `PATH`, point the host at the
binary directly:

```powershell
$env:RTMS_OPENOCD_BIN = "C:\openocd\xpack-openocd-0.12.0-7\bin\openocd.exe"
$env:OPENOCD_SCRIPTS = "C:\openocd\xpack-openocd-0.12.0-7\openocd\scripts"
```

## Local Demo Mode

To exercise the flow without hardware:

```bash
export RTMS_SIMULATE_HARDWARE=1
export RTMS_SIMULATE_CAPTURE=1
```

This keeps the end-to-end job/session/report path usable for development.

## Tests

```bash
pytest
```

## Logging-Spec Integration

When the firmware logging contract changes:

1. Update the `MLOG` decode rules in `server/app/services/parsing.py`.
2. Keep report output limited to fields explicitly present in the binary stream.
3. Add decoder fixtures/tests that encode the exact wire layout and failure cases.
