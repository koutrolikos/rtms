# Operator Guide

## Normal Workflow

1. Start the server on the control machine or VPS.
2. Open the web UI using the server's LAN/VPS address, not `localhost`.
3. Start one agent on the TX host and one agent on the RX host with `RANGE_TEST_SERVER_URL` pointed at that server address.
4. Assign TX and RX hosts.
5. From the session page, search or paste the exact git SHA, load the repo's build defaults for that commit, select the role and firmware config, then queue the build on a build-capable agent. Or upload a prebuilt local ELF from an agent.
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
- the uploaded build log when the failure happened during artifact build
- the raw OpenOCD log
- the agent event log
- the timing sample file

## Notes

- The Hosts page shows the agent/public URL the server is advertising for remote agents.
- Session start notes remain editable after the session ends.
- Manual stop is available while the session is capturing.
- Raw artifacts are always preserved, even if parsing or report generation is imperfect.

## Prebuilt ELF Upload

If a repo cannot be built from a clean agent clone using the configured recipe, upload a prebuilt ELF as a
manual session artifact:

```bash
range-test-agent upload-prebuilt \
  --session-id <session-id> \
  --role TX \
  --elf-path /path/to/tx.elf \
  --git-sha <base-head-sha> \
  --source-repo owner/repo \
  --dirty-worktree
```

Repeat for `RX`, then assign both ready artifacts from the session page before
starting the session.
