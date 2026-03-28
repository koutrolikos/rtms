# RTMS

## Fresh Install (Step By Step)

Use these commands exactly, in order.

### 1) Pick your server URL

Example used below:

- `http://172.20.10.3:8000`

### 2) Clone RTMS

```bash
git clone https://github.com/koutrolikos/rtms.git
cd rtms
```

### 3) Run the bootstrap script for your OS

What bootstrap does now:

- checks required dependencies
- installs missing dependencies
- clones/updates RTMS into `~/rtms-host`
- writes runtime env file (`.rtms-env.sh` or `.rtms-env.ps1`)
- does not create a virtual environment

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

### 4) Create venv and install RTMS package

macOS:

```bash
cd ~/rtms-host
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
source .rtms-env.sh
```

Linux:

```bash
cd ~/rtms-host
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
source .rtms-env.sh
```

Windows PowerShell:

```powershell
cd ~/rtms-host
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
. .\.rtms-env.ps1
```

### 5) Run

Start host:

macOS/Linux:

```bash
./.venv/bin/rtms-host run
```

Windows PowerShell:

```powershell
.\.venv\Scripts\rtms-host.exe run
```

Start server (optional on this host):

macOS/Linux:

```bash
./.venv/bin/rtms-server run
```

Windows PowerShell:

```powershell
.\.venv\Scripts\rtms-server.exe run
```

Distributed RF range-test orchestration MVP with:

- a FastAPI control plane
- polling Python hosts
- session-scoped artifact storage
- OpenOCD-compatible flash/verify orchestration
- capture coordination across TX/RX hosts
- raw-log preservation, parsing, merge, and HTML report generation

## One Command Sanity Check

From the host machine:

```bash
curl http://172.20.10.3:8000/healthz
```

You should get a healthy response before starting the host.

## Authentication

RTMS now supports optional HTTP Basic auth for both the web UI and the host API.

Enable it on the server:

```bash
export RTMS_AUTH_USERNAME="rtms"
export RTMS_AUTH_PASSWORD="change-me"
```

When auth is enabled, configure every host with matching credentials:

```bash
export RTMS_SERVER_USERNAME="rtms"
export RTMS_SERVER_PASSWORD="change-me"
```

Notes:

- `/healthz` remains open for simple liveness checks.
- All other operator and host endpoints require Basic auth.
- Browser access will prompt for credentials automatically.
- CLI checks can use `curl -u rtms:change-me http://172.20.10.3:8000/sessions`.

## Dependency Matrix (What Is Actually Required)

Install only what you need for the capabilities enabled on that host.

- Core host (always): Python 3.11+, pip/venv, network access to server
- Build-capable host: `git` plus repo-specific build tools
- Current High-Altitude-CC build recipe: `cmake`, `arm-none-eabi-gcc`
- Flash-capable host: `openocd` plus correct target/interface scripts
- Capture-capable host: built-in OpenOCD RTT capture shipped with the host

Built-in capture defaults:

- search for RTT control block ID `SEGGER RTT` from `0x20000000` for `131072` bytes
- capture human RTT from channel `0` into `rtt.log`
- capture binary `MLOG` RTT data from channel `1` into `rtt.rttbin`
- capture OpenOCD stdout/stderr into `capture-command.log`

`RTMS_CAPTURE_COMMAND_TEMPLATE` remains available as an override if you need a custom capture flow.

Minimal capability env vars:

```bash
export RTMS_HOST_BUILD_CAPABLE=1
export RTMS_HOST_FLASH_CAPABLE=1
export RTMS_HOST_CAPTURE_CAPABLE=1
```

If this host should only build and never flash:

```bash
export RTMS_HOST_BUILD_CAPABLE=1
export RTMS_HOST_FLASH_CAPABLE=0
export RTMS_HOST_CAPTURE_CAPABLE=0
```

If this host should only flash/capture and never build:

```bash
export RTMS_HOST_BUILD_CAPABLE=0
export RTMS_HOST_FLASH_CAPABLE=1
export RTMS_HOST_CAPTURE_CAPABLE=1
```

1. Create a virtual environment and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

2. Start the server:

```bash
rtms-server
```

The server listens on all interfaces by default and advertises a LAN-facing URL.
If you need to force the public address used by hosts, set:

```bash
export RTMS_SERVER_PUBLIC_BASE_URL="http://172.20.10.3:8000"
```

By default, the bootstrap-generated env file pins:

- `RTMS_HOST_DATA_DIR=~/rtms-host/host_data`
- `RTMS_SERVER_DATA_DIR=~/rtms-host/server_data`

If you do a manual editable install instead of bootstrap, set those explicitly if you do
not want runtime data created relative to the directory where you launch the command.

1. Start an host:

```bash
export RTMS_SERVER_URL="http://172.20.10.3:8000"
rtms-host run
```

If the host is running on the same machine as the server during development,
use:

```bash
export RTMS_SERVER_URL="http://127.0.0.1:8000"
```

If server auth is enabled, also set:

```bash
export RTMS_SERVER_USERNAME="rtms"
export RTMS_SERVER_PASSWORD="change-me"
```

On Windows PowerShell:

```powershell
$env:RTMS_SERVER_URL = "http://172.20.10.3:8000"
$env:RTMS_OPENOCD_TARGET_CFG = "target/stm32g4x.cfg"
rtms-host run
```

If the Windows host reports `WinError 2` while launching OpenOCD, the binary is
not installed or not on `PATH`. Set `RTMS_OPENOCD_BIN` to the full binary
path, for example:

```powershell
$env:RTMS_OPENOCD_BIN = "C:\openocd\xpack-openocd-0.12.0-7\bin\openocd.exe"
$env:OPENOCD_SCRIPTS = "C:\openocd\xpack-openocd-0.12.0-7\openocd\scripts"
```

4. Open `http://<server-lan-ip>:8000` from any device on the same network.

5. From another device, verify the server is reachable:

```bash
curl http://<server-lan-ip>:8000/healthz
```

If the Windows host shows `WinError 10061`, it usually means one of these:

- `RTMS_SERVER_URL` still points to `127.0.0.1`, `localhost`, or `0.0.0.0`
- the server machine firewall is blocking inbound TCP `8000`
- the host is using the wrong LAN IP for the server machine

That first point only applies to a remote host. For same-machine development,
`http://127.0.0.1:8000` is valid.

The bundled `High-Altitude-CC` example repo is built from the session page by:

- choosing an exact git commit
- loading the commit's `Core/Inc/app_config.h` defaults from GitHub
- selecting TX or RX plus the exposed firmware config fields
- queueing the server-owned build job on a build-capable host

The host builds that repo through
`rtms-host build-high-altitude-cc --source . --build-dir build/debug`
with generated role/build-config JSON from the session form, patches
`Core/Inc/app_config.h` inside the clean clone, uploads the bundle and build
log, then deletes its local checkout and build files for that artifact.

If the host shows `SSL: WRONG_VERSION_NUMBER`, it is almost certainly using
`https://<server-ip>:8000` against this plain HTTP server. Use `http://`, for example:

```powershell
$env:RTMS_SERVER_URL = "http://172.20.10.3:8000"
```

## Prebuilt Artifact Upload

If a repo cannot be built from a clean clone using the configured server recipe,
you can upload an existing ELF as a
session-scoped artifact:

```bash
rtms-host upload-prebuilt \
  --session-id <session-id> \
  --role TX \
  --elf-path ~/High-Altitude-CC/build/tx/debug/High-Altitude-CC.elf \
  --git-sha eb1f1d5bf845bae78bb6e1427b145a75f970a079 \
  --source-repo koutrolikos/High-Altitude-CC \
  --dirty-worktree
```

```bash
rtms-host upload-prebuilt \
  --session-id <session-id> \
  --role RX \
  --elf-path ~/High-Altitude-CC/build/rx/debug/High-Altitude-CC.elf \
  --git-sha eb1f1d5bf845bae78bb6e1427b145a75f970a079 \
  --source-repo koutrolikos/High-Altitude-CC \
  --dirty-worktree
```

The command prints the created `artifact_id`. After upload, assign the ready TX
and RX artifacts from the session page.

See [architecture.md](/Users/odysseaskoutrolikos/rtms/architecture.md), [host.md](/Users/odysseaskoutrolikos/rtms/host.md), [mvp_scope.md](/Users/odysseaskoutrolikos/rtms/mvp_scope.md), [docs/developer_setup.md](/Users/odysseaskoutrolikos/rtms/docs/developer_setup.md), and [docs/operator_guide.md](/Users/odysseaskoutrolikos/rtms/docs/operator_guide.md).
