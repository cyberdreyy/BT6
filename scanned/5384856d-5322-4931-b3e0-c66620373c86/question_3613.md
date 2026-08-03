# Q3613: createPatchTraceResult partial failures are treated as successful state

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `createPatchTraceResult` keep partial transfer or trace state and later treat it as a successful completed operation?

## Target
- File/function: network/gitlab.go: createPatchTraceResult
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, partial failures and later completion logic
- Exploit idea: smuggle partial results past failure handling into later success handling
- Invariant to test: partial failures must not be promoted to successful logical state
- Expected Immunefi impact: output tampering or stale-state reuse
- Fast validation: trigger partial failures and verify completion state stays failed or is fully rebuilt
