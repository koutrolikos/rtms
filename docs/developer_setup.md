# Developer Setup

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
agent. If a firmware repo depends on generated IDE outputs, untracked makefiles,
local toolchain wrappers, or other files that are not present in Git, the agent
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
make -f Debug/makefile DEBUG=1 all hex bin
```

RTMS resolves the operator-selected TX/RX role plus the exposed firmware build
config into `CDEFS_EXTRA` overrides, queues the build job from the server UI,
uploads the build log as a raw artifact, and removes the agent's local build
workspace after a successful upload.

## Run Server

```bash
range-test-server
```

By default the server binds to `0.0.0.0:8000` and auto-detects a LAN URL for agent-facing links.
If the detected address is wrong, set:

```bash
export RANGE_TEST_SERVER_PUBLIC_BASE_URL="http://192.168.1.50:8000"
```

Key env vars:

- `RANGE_TEST_SERVER_HOST`
- `RANGE_TEST_SERVER_PORT`
- `RANGE_TEST_SERVER_PUBLIC_BASE_URL`
- `RANGE_TEST_SERVER_DATA_DIR`
- `RANGE_TEST_SERVER_DB_URL`
- `RANGE_TEST_REPO_CONFIG`
- `GITHUB_TOKEN`

## Run Agent

```bash
export RANGE_TEST_SERVER_URL="http://192.168.1.50:8000"
range-test-agent run
```

If the agent is running on the same machine as the server for local
development, use:

```bash
export RANGE_TEST_SERVER_URL="http://127.0.0.1:8000"
```

From Windows PowerShell:

```powershell
$env:RANGE_TEST_SERVER_URL = "http://192.168.1.50:8000"
$env:RANGE_TEST_OPENOCD_TARGET_CFG = "target/stm32g4x.cfg"
range-test-agent run
```

Verify connectivity from the agent machine before starting the agent:

```bash
curl http://192.168.1.50:8000/healthz
```

Key env vars:

- `RANGE_TEST_SERVER_URL`
- `RANGE_TEST_AGENT_NAME`
- `RANGE_TEST_AGENT_LABEL`
- `RANGE_TEST_AGENT_BUILD_CAPABLE`
- `RANGE_TEST_SIMULATE_HARDWARE`
- `RANGE_TEST_SIMULATE_CAPTURE`
- `RANGE_TEST_OPENOCD_BIN`
- `RANGE_TEST_OPENOCD_INTERFACE_CFG`
- `RANGE_TEST_OPENOCD_TARGET_CFG`
- `RANGE_TEST_CAPTURE_COMMAND_TEMPLATE`

For STM32G474-based boards, the correct default OpenOCD target script is:

```bash
export RANGE_TEST_OPENOCD_TARGET_CFG="target/stm32g4x.cfg"
```

If OpenOCD is installed on Windows but not on `PATH`, point the agent at the
binary directly:

```powershell
$env:RANGE_TEST_OPENOCD_BIN = "C:\openocd\xpack-openocd-0.12.0-7\bin\openocd.exe"
$env:OPENOCD_SCRIPTS = "C:\openocd\xpack-openocd-0.12.0-7\openocd\scripts"
```

## Local Demo Mode

To exercise the flow without hardware:

```bash
export RANGE_TEST_SIMULATE_HARDWARE=1
export RANGE_TEST_SIMULATE_CAPTURE=1
```

This keeps the end-to-end job/session/report path usable for development.

## Tests

```bash
pytest
```

## Logging-Spec Integration

When the authoritative firmware logging document arrives:

1. Update the parser rules in `server/app/services/parsing.py`.
2. Update metric computation in the same module.
3. Add parser fixtures/tests that encode the spec’s exact line formats and expected metrics.
