# Architecture

## Shape

- `server/` is the control plane: session lifecycle, job dispatch, artifact storage, parsing, merge, report generation, and the operator UI.
- `agent/` is the host-side worker: registration, heartbeat, build execution, artifact download, OpenOCD flash/verify, capture orchestration, and raw artifact upload.
- `shared/` contains the typed protocol and state-machine contracts shared by both sides.

## Runtime Model

- One active session at a time for the MVP.
- The server is the source of truth for session state, selected artifacts, job dispatch, and final report output.
- Agents poll the server for work. This keeps the protocol simple and VPS-friendly.
- Jobs are explicit objects with `pending -> running -> completed|failed|cancelled` transitions.
- Each role run uses an explicit execution lifecycle:
  `idle -> assigned -> artifact_pending -> artifact_ready -> flashing -> flash_verified -> prepare_capture -> capture_ready -> capturing -> completed|failed`

## Storage

- Default persistence is SQLite plus local filesystem storage under `server_data/`.
- Artifact bundles are stored as session-scoped zip files with a `manifest.json`.
- Raw logs are stored exactly as captured:
  RTT logs, OpenOCD logs, agent event logs, timing sample files, and generated parser output.

## Coordination

- The operator assigns TX/RX hosts and TX/RX artifacts in the web UI.
- Starting a session creates `prepare_role` jobs for both hosts.
- When both hosts report `capture_ready`, the server computes a coordinated future `planned_start_at` and dispatches `start_capture` jobs to both hosts.
- Manual stop is handled through separate `stop_capture` jobs.

## Timing

- Agents sample server time and estimate offset using a midpoint method.
- Offset samples are preserved in diagnostics and uploaded timing artifacts.
- The parser corrects host timestamps using stored agent offset samples before merge.
- Relative timestamps are aligned to the coordinated capture start time when possible.

## Parsing And Reports

- Parsing is layered: raw preservation, line parsing, timestamp correction, merged timeline, packet correlation, metric computation, report rendering.
- The current parser is a compatibility-focused baseline because the authoritative firmware logging document was not present in this workspace.
- The parser entry point is isolated in `server/app/services/parsing.py`, so the authoritative log/metric rules can replace the baseline logic without changing the rest of the architecture.

