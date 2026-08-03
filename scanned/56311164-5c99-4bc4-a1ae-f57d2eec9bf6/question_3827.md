# Q3827: Abort prior attempt state survives into a later retry

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `Abort` keep state from a failed attempt and apply it to a later logical retry?

## Target
- File/function: common/trace.go: Abort
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, failed attempts and later retries
- Exploit idea: persist failed-attempt state and let the next attempt trust it
- Invariant to test: failed attempts must not leak mutable state into later retries
- Expected Immunefi impact: wrong-target mutation or output tampering
- Fast validation: force retries after partial failure and verify no prior mutable state survives
