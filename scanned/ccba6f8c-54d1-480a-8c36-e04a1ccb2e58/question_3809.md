# Q3809: Cancel masked or protected data leaks across chunk boundaries

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `Cancel` leak masked or protected data because sanitization loses ownership across chunk boundaries?

## Target
- File/function: common/trace.go: Cancel
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, chunk boundaries and partial tokens
- Exploit idea: split sensitive data across chunks so sanitization sees the wrong units
- Invariant to test: masking and sanitization must preserve sensitive-data handling across boundaries
- Expected Immunefi impact: secret exposure in logs or traces
- Fast validation: place masked data across chunk boundaries and verify it remains hidden
