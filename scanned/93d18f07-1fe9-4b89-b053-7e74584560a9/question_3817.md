# Q3817: Cancel per-job identity state is reused across jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `Cancel` reuse mutable identity state from one job in another job handled by the same runner process?

## Target
- File/function: common/trace.go: Cancel
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, overlapping or rapid sequential jobs
- Exploit idea: leave mutable per-job state in shared process memory between jobs
- Invariant to test: per-job mutable state must be fully isolated or reset between jobs
- Expected Immunefi impact: cross-job hijack or stale-state trust
- Fast validation: run rapid sequential jobs and verify no identity state leaks forward
