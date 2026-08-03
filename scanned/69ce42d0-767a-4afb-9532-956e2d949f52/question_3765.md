# Q3765: Fail retried body is reused after target changes

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `Fail` retry with a body or pointer prepared for one logical target after the target changed?

## Target
- File/function: common/trace.go: Fail
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, target changes between retries
- Exploit idea: carry prior request body state into a later different target
- Invariant to test: retried bodies must remain bound to their original logical target
- Expected Immunefi impact: wrong-target transfer or stale-state reuse
- Fast validation: mutate the logical target between retries and verify rebinding or rejection
