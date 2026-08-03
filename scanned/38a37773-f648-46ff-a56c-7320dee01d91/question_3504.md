# Q3504: RequestJob cancel, abort, or finish hits the wrong live job

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `RequestJob` cancel, abort, or finish a different live job than the one that originated the request?

## Target
- File/function: network/gitlab.go: RequestJob
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, overlapping live jobs and reconnect timing
- Exploit idea: desynchronize job identity from the state-transition request
- Invariant to test: state transitions must stay bound to the live job that authorized them
- Expected Immunefi impact: job hijack or unauthorized job-state mutation
- Fast validation: overlap jobs and verify state transitions cannot cross identities
