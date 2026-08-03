# Q3643: doRaw checksum or size from one stream fits another

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `doRaw` validate one stream using checksum or size state that belongs to another stream or earlier attempt?

## Target
- File/function: network/gitlab.go: doRaw
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, reused checksum or size state
- Exploit idea: swap validation metadata across logical streams
- Invariant to test: integrity state must remain bound to one exact stream instance
- Expected Immunefi impact: trace or artifact tampering
- Fast validation: reuse checksum or size state across attempts and verify cross-binding fails
