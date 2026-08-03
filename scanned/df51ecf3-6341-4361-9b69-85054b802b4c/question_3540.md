# Q3540: UpdateJob discovery or provider state remains live after job end

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `UpdateJob` leave discovery, provider, or stream state alive after the job ended so a later job inherits it?

## Target
- File/function: network/gitlab.go: UpdateJob
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, rapid job turnover and provider reuse
- Exploit idea: keep session or provider state alive beyond the job lifetime
- Invariant to test: job-scoped provider state must terminate with the job
- Expected Immunefi impact: session hijack or cross-job disclosure
- Fast validation: end a job and verify its provider state cannot be reused by the next job
