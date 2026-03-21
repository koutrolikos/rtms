# RTMS

## Start Here (New User)

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
./scripts/bootstrap_agent_macos.sh --server-url http://172.20.10.3:8000
```

Linux:

```bash
./scripts/bootstrap_agent_linux.sh --server-url http://172.20.10.3:8000
```

Windows PowerShell:

```powershell
.\scripts\bootstrap_agent_windows.ps1 -ServerUrl http://172.20.10.3:8000
```

The script installs dependencies and prepares Python.
On macOS/Linux it also installs command shims, so after it finishes you can open a new terminal and run `range-test-server run` or `range-test-agent run` directly.

Distributed RF range-test orchestration MVP with:

- a FastAPI control plane
- polling Python host agents
- session-scoped artifact storage
- OpenOCD-compatible flash/verify orchestration
- capture coordination across TX/RX hosts
- raw-log preservation, parsing, merge, and HTML report generation

## Zero To Agent (Copy-Paste Edition)

If you have a fresh machine and want to run an agent with minimal thinking, use this.

### 0) One-command bootstrap scripts (recommended)

Linux:

```bash
./scripts/bootstrap_agent_linux.sh --server-url http://172.20.10.3:8000
```

macOS:

```bash
./scripts/bootstrap_agent_macos.sh --server-url http://172.20.10.3:8000
```

Windows PowerShell:

```powershell
.\scripts\bootstrap_agent_windows.ps1 -ServerUrl http://172.20.10.3:8000
```

All scripts install dependencies, clone/update RTMS into `~/rtms-agent`, create a venv,
install the package, write an env file, and pin runtime data under that install directory
by default instead of the caller's current working directory.

### 1) Pick your server URL

This is the RTMS server address agents will call, for example:

- `http://172.20.10.3:8000` (LAN)
- `http://172.20.10.3:8000` (LAN)

Do not use `https://` unless your server is actually configured for TLS.

### 2) Linux (fresh machine)

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl openocd make cmake gcc-arm-none-eabi

git clone https://github.com/koutrolikos/rtms.git
cd rtms
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

export RANGE_TEST_SERVER_URL="http://172.20.10.3:8000"
export RANGE_TEST_OPENOCD_TARGET_CFG="target/stm32g4x.cfg"
range-test-agent run
```

Fedora/RHEL:

```bash
sudo dnf install -y python3 python3-pip git curl openocd make cmake arm-none-eabi-gcc-cs

git clone https://github.com/koutrolikos/rtms.git
cd rtms
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

export RANGE_TEST_SERVER_URL="http://172.20.10.3:8000"
export RANGE_TEST_OPENOCD_TARGET_CFG="target/stm32g4x.cfg"
range-test-agent run
```

### 3) Windows (fresh machine, PowerShell)

Install tools (Winget):

```powershell
winget install -e --id Python.Python.3.11
winget install -e --id Git.Git
winget install -e --id xpack-dev-tools.OpenOCD
```

Then:

```powershell
git clone https://github.com/koutrolikos/rtms.git
cd rtms
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

$env:RANGE_TEST_SERVER_URL = "http://172.20.10.3:8000"
$env:RANGE_TEST_OPENOCD_TARGET_CFG = "target/stm32g4x.cfg"
range-test-agent run
```

If OpenOCD is installed but not on PATH:

```powershell
$env:RANGE_TEST_OPENOCD_BIN = "C:\openocd\xpack-openocd-0.12.0-7\bin\openocd.exe"
$env:OPENOCD_SCRIPTS = "C:\openocd\xpack-openocd-0.12.0-7\openocd\scripts"
```

### 4) One command sanity check

From the agent machine:

```bash
curl http://172.20.10.3:8000/healthz
```

You should get a healthy response before starting the agent.

## Dependency Matrix (What Is Actually Required)

Install only what you need for the capabilities enabled on that host.

- Core agent (always): Python 3.11+, pip/venv, network access to server
- Build-capable agent: `git` plus repo-specific build tools
- Current High-Altitude-CC build recipe: `cmake`, `arm-none-eabi-gcc`
- Flash-capable agent: `openocd` plus correct target/interface scripts
- Capture-capable agent: tool referenced by `RANGE_TEST_CAPTURE_COMMAND_TEMPLATE` (if not simulating capture)

Minimal capability env vars:

```bash
export RANGE_TEST_AGENT_BUILD_CAPABLE=1
export RANGE_TEST_AGENT_FLASH_CAPABLE=1
export RANGE_TEST_AGENT_CAPTURE_CAPABLE=1
```

If this host should only build and never flash:

```bash
export RANGE_TEST_AGENT_BUILD_CAPABLE=1
export RANGE_TEST_AGENT_FLASH_CAPABLE=0
export RANGE_TEST_AGENT_CAPTURE_CAPABLE=0
```

If this host should only flash/capture and never build:

```bash
export RANGE_TEST_AGENT_BUILD_CAPABLE=0
export RANGE_TEST_AGENT_FLASH_CAPABLE=1
export RANGE_TEST_AGENT_CAPTURE_CAPABLE=1
```

1. Create a virtual environment and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

2. Start the server:

```bash
range-test-server
```

The server listens on all interfaces by default and advertises a LAN-facing URL.
If you need to force the public address used by agents, set:

```bash
export RANGE_TEST_SERVER_PUBLIC_BASE_URL="http://172.20.10.3:8000"
```

By default, the bootstrap-generated env file pins:

- `RANGE_TEST_AGENT_DATA_DIR=~/rtms-agent/agent_data`
- `RANGE_TEST_SERVER_DATA_DIR=~/rtms-agent/server_data`

If you do a manual editable install instead of bootstrap, set those explicitly if you do
not want runtime data created relative to the directory where you launch the command.

3. Start an agent:

```bash
export RANGE_TEST_SERVER_URL="http://172.20.10.3:8000"
range-test-agent run
```

If the agent is running on the same machine as the server during development,
use:

```bash
export RANGE_TEST_SERVER_URL="http://127.0.0.1:8000"
```

On Windows PowerShell:

```powershell
$env:RANGE_TEST_SERVER_URL = "http://172.20.10.3:8000"
$env:RANGE_TEST_OPENOCD_TARGET_CFG = "target/stm32g4x.cfg"
range-test-agent run
```

If the Windows agent reports `WinError 2` while launching OpenOCD, the binary is
not installed or not on `PATH`. Set `RANGE_TEST_OPENOCD_BIN` to the full binary
path, for example:

```powershell
$env:RANGE_TEST_OPENOCD_BIN = "C:\openocd\xpack-openocd-0.12.0-7\bin\openocd.exe"
$env:OPENOCD_SCRIPTS = "C:\openocd\xpack-openocd-0.12.0-7\openocd\scripts"
```

4. Open `http://<server-lan-ip>:8000` from any device on the same network.

5. From another device, verify the server is reachable:

```bash
curl http://<server-lan-ip>:8000/healthz
```

If the Windows agent shows `WinError 10061`, it usually means one of these:

- `RANGE_TEST_SERVER_URL` still points to `127.0.0.1`, `localhost`, or `0.0.0.0`
- the server machine firewall is blocking inbound TCP `8000`
- the agent is using the wrong LAN IP for the server machine

That first point only applies to a remote agent. For same-machine development,
`http://127.0.0.1:8000` is valid.

The bundled `High-Altitude-CC` example repo is built from the session page by:

- choosing an exact git commit
- loading the commit's `Core/Inc/app_config.h` defaults from GitHub
- selecting TX or RX plus the exposed firmware config fields
- queueing the server-owned build job on a build-capable agent

The agent builds that repo through
`range-test-agent build-high-altitude-cc --source . --build-dir build/debug`
with generated role/build-config JSON from the session form, patches
`Core/Inc/app_config.h` inside the clean clone, uploads the bundle and build
log, then deletes its local checkout and build files for that artifact.

If the agent shows `SSL: WRONG_VERSION_NUMBER`, it is almost certainly using
`https://<server-ip>:8000` against this plain HTTP server. Use `http://`, for example:

```powershell
$env:RANGE_TEST_SERVER_URL = "http://172.20.10.3:8000"
```

## Prebuilt Artifact Upload

If a repo cannot be built from a clean clone using the configured server recipe,
you can upload an existing ELF as a
session-scoped artifact:

```bash
range-test-agent upload-prebuilt \
  --session-id <session-id> \
  --role TX \
  --elf-path ~/High-Altitude-CC/build/tx/debug/High-Altitude-CC.elf \
  --git-sha eb1f1d5bf845bae78bb6e1427b145a75f970a079 \
  --source-repo koutrolikos/High-Altitude-CC \
  --dirty-worktree
```

```bash
range-test-agent upload-prebuilt \
  --session-id <session-id> \
  --role RX \
  --elf-path ~/High-Altitude-CC/build/rx/debug/High-Altitude-CC.elf \
  --git-sha eb1f1d5bf845bae78bb6e1427b145a75f970a079 \
  --source-repo koutrolikos/High-Altitude-CC \
  --dirty-worktree
```

The command prints the created `artifact_id`. After upload, assign the ready TX
and RX artifacts from the session page.

See [architecture.md](/Users/odysseaskoutrolikos/rtms/architecture.md), [agent.md](/Users/odysseaskoutrolikos/rtms/agent.md), [mvp_scope.md](/Users/odysseaskoutrolikos/rtms/mvp_scope.md), [docs/developer_setup.md](/Users/odysseaskoutrolikos/rtms/docs/developer_setup.md), and [docs/operator_guide.md](/Users/odysseaskoutrolikos/rtms/docs/operator_guide.md).
