# MVP Scope

## Implemented

- FastAPI server with SQLite/file-storage defaults
- Polling Python agent
- Agent registration and heartbeat
- Host overview page
- Session creation and edit flow
- TX/RX host assignment
- Session-scoped artifact records and bundle storage
- GitHub repo config and commit-browse API hooks
- Build-job dispatch to selected agents
- Agent local build-and-upload command
- Artifact distribution from server to agents
- OpenOCD-compatible flash/verify execution path
- Coordinated capture start across two roles
- Manual stop job dispatch
- Raw artifact upload and inspection/download
- Session/role/job state tracking with diagnostics
- Baseline parser, time correction, merge, packet correlation, and HTML report generation
- Developer/operator docs and tests

## Intentionally Not Implemented

- Authentication and RBAC
- Multiple simultaneous active sessions
- Websocket streaming dashboards
- Persistent hardware identity across sessions
- Additional flashing backends beyond OpenOCD
- Permanent artifact registry
- CI/CD integration
- GIS/map-heavy visualization
- Advanced scheduling across many hosts

## Assumption Boundary

- The authoritative firmware logging/metrics Markdown document was not present in this workspace during implementation.
- The parser/report pipeline is therefore intentionally isolated and compatibility-focused, so the authoritative rules can be dropped into `server/app/services/parsing.py` without changing the session, artifact, agent, or report architecture.

