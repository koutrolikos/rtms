# RF Range-Test MVP

Distributed RF range-test orchestration MVP with:

- a FastAPI control plane
- polling Python host agents
- session-scoped artifact storage
- OpenOCD-compatible flash/verify orchestration
- capture coordination across TX/RX hosts
- raw-log preservation, parsing, merge, and HTML report generation

## Quick start

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
export RANGE_TEST_SERVER_PUBLIC_BASE_URL="http://192.168.1.50:8000"
```

3. Start an agent:

```bash
export RANGE_TEST_SERVER_URL="http://192.168.1.50:8000"
range-test-agent run
```

If the agent is running on the same machine as the server during development,
use:

```bash
export RANGE_TEST_SERVER_URL="http://127.0.0.1:8000"
```

On Windows PowerShell:

```powershell
$env:RANGE_TEST_SERVER_URL = "http://192.168.1.50:8000"
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

The agent builds that repo through `make -f Debug/makefile DEBUG=1 all hex bin`
with generated `CDEFS_EXTRA` overrides, uploads the bundle and build log, then
deletes its local checkout and build files for that artifact.

If the agent shows `SSL: WRONG_VERSION_NUMBER`, it is almost certainly using
`https://<server-ip>:8000` against this plain HTTP server. Use `http://`, for example:

```powershell
$env:RANGE_TEST_SERVER_URL = "http://192.168.1.50:8000"
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
