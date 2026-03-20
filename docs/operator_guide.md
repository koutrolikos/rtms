# Operator Guide

## Normal Workflow

1. Start the server on the VPS.
2. Start one agent on the TX host and one agent on the RX host.
3. Open the web UI and create a session.
4. Assign TX and RX hosts.
5. Build artifacts from an exact GitHub SHA or upload/build a local session artifact from an agent.
6. Assign a ready TX artifact and a ready RX artifact.
7. Start the session.
8. Wait for both roles to prepare and for coordinated capture to begin.
9. Add annotations during the run.
10. After completion, open the generated report.

## Failure Visibility

The session detail page preserves:

- role status
- flash/verify status
- failure reason
- session event log
- raw artifacts for download

If flashing or capture fails, inspect:

- the role run row on the session page
- the raw OpenOCD log
- the agent event log
- the timing sample file

## Notes

- Session start notes remain editable after the session ends.
- Manual stop is available while the session is capturing.
- Raw artifacts are always preserved, even if parsing or report generation is imperfect.

