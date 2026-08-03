# Q3595: patchTraceQuery one job output influences another live stream

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `patchTraceQuery` mix output from one job into the live trace or transfer state of another job on the same runner?

## Target
- File/function: network/gitlab.go: patchTraceQuery
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, overlapping jobs and shared transport state
- Exploit idea: cross-bind shared transport state across live jobs
- Invariant to test: transport state must remain isolated per live job
- Expected Immunefi impact: cross-job output tampering or disclosure
- Fast validation: run overlapping jobs and verify their transport state is isolated
