# Developer Setup

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Configure Repos

The server reads repo definitions from `server_data/repos.json` by default.

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

## Run Server

```bash
range-test-server
```

Key env vars:

- `RANGE_TEST_SERVER_HOST`
- `RANGE_TEST_SERVER_PORT`
- `RANGE_TEST_SERVER_DATA_DIR`
- `RANGE_TEST_SERVER_DB_URL`
- `RANGE_TEST_REPO_CONFIG`
- `GITHUB_TOKEN`

## Run Agent

```bash
range-test-agent run
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

