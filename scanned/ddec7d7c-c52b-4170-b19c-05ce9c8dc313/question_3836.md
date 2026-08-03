# Q3836: Abort body regeneration reads a mutated file version

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `Abort` regenerate a request body from a local file that changed after validation?

## Target
- File/function: common/trace.go: Abort
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, body regeneration and local file mutation
- Exploit idea: use retries to read a different local file version than the one initially approved
- Invariant to test: regenerated bodies must remain bound to the same validated file identity
- Expected Immunefi impact: wrong-file transfer or artifact tampering
- Fast validation: mutate the local file between retries and verify regeneration detects it
