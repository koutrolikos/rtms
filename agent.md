# Agent Protocol And State

## Registration

- The agent starts with `range-test-agent run`.
- It registers once with `/api/agent/register`.
- It heartbeats periodically to `/api/agent/heartbeat`.
- It samples `/api/agent/time-sync` and includes its latest offset estimate in heartbeat diagnostics.

## Polling

- The agent polls `/api/agent/poll` for one queued job at a time.
- Supported job types:
  - `build_artifact`
  - `prepare_role`
  - `start_capture`
  - `stop_capture`

## Local State

- Prepared role context is stored under `agent_data/state/contexts/`.
- Per-session-role working directories live under `agent_data/sessions/<session>/<role>/`.
- The agent preserves:
  - downloaded artifact bundle
  - extracted manifest/files
  - OpenOCD log
  - agent event log
  - timing sample file
  - RTT capture log

## Job Semantics

### `build_artifact`

- Checkout the configured repo at the exact SHA.
- Run the configured build recipe.
- Package the build outputs into an artifact bundle zip with `manifest.json`.
- Upload the bundle back to the server.

### `prepare_role`

- Download the assigned artifact bundle from the server.
- Extract the bundle.
- Resolve the flash image and ELF metadata from the manifest.
- Run OpenOCD `program + verify`.
- Preserve OpenOCD and local event logs.
- Upload side-effect logs to the server.

### `start_capture`

- Load the prepared role context.
- Wait until the coordinated `planned_start_at`.
- Start capture using either:
  - a configured external capture command template, or
  - the simulation path for local development
- Preserve RTT logs and local event/timing logs.
- Upload the raw logs on completion.

### `stop_capture`

- Find the running capture for the session role.
- Send termination to the capture process.
- Report whether the stop request was applied.

