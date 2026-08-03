# Q3621: ProcessJob stale trace offset replay is accepted

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `ProcessJob` accept replayed or stale offsets for job trace and job-state updates?

## Target
- File/function: network/gitlab.go: ProcessJob
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, stale offsets
- Exploit idea: replay earlier progress state so later writes land at an attacker-chosen position
- Invariant to test: the live job identity, accepted offsets, and final visible output must reject stale or replayed positions
- Expected Immunefi impact: job or trace hijack and output tampering
- Fast validation: replay old offsets and verify the update is rejected
