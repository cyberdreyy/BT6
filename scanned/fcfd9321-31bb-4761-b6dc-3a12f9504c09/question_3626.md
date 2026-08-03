# Q3626: ProcessJob parallel artifact transfer mixes object versions

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `ProcessJob` mix bytes from different artifact versions into one trusted result during parallel transfer?

## Target
- File/function: network/gitlab.go: ProcessJob
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, object changes during parallel transfer
- Exploit idea: swap artifact versions across parallel chunk boundaries
- Invariant to test: all transferred bytes must come from one bound object version
- Expected Immunefi impact: artifact tampering or wrong-file disclosure
- Fast validation: change artifact versions during transfer and verify mixed results are rejected
