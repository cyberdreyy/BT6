# Q3510: RequestJob normalized URIs collapse distinct targets

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `RequestJob` treat two distinct URIs or logical targets as equivalent after normalization?

## Target
- File/function: network/gitlab.go: RequestJob
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, equivalent-looking URIs or paths
- Exploit idea: collapse distinct targets through URI normalization so state crosses boundaries
- Invariant to test: URI normalization must not merge distinct security principals or logical objects
- Expected Immunefi impact: wrong-target transfer or artifact or trace confusion
- Fast validation: exercise equivalent-looking URIs and verify distinct target binding
