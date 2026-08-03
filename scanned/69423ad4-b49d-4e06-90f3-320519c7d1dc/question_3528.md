# Q3528: UpdateJob response details from one op govern another

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `UpdateJob` apply response status, headers, or details from one logical operation to another?

## Target
- File/function: network/gitlab.go: UpdateJob
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, overlapping operations and reordered responses
- Exploit idea: mix response handling across concurrent or retried operations
- Invariant to test: response handling must stay bound to the originating request
- Expected Immunefi impact: wrong-state acceptance or transfer confusion
- Fast validation: overlap operations and verify responses never cross-bind
