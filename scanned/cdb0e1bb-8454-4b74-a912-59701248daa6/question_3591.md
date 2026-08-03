# Q3591: patchTraceQuery read-logs or session provider serves the wrong job

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `patchTraceQuery` return log or session data for the wrong live job after reconnects or rapid job turnover?

## Target
- File/function: network/gitlab.go: patchTraceQuery
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, reconnects and rapid job turnover
- Exploit idea: hold onto provider state after job identity changed
- Invariant to test: log or session providers must stay bound to the live job identity
- Expected Immunefi impact: session hijack or cross-job log disclosure
- Fast validation: turn jobs over quickly and verify providers never cross job identities
