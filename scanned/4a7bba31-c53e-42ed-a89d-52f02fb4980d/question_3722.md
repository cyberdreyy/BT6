# Q3722: Write out-of-order chunk writes tamper final output

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `Write` accept chunks out of order so final output differs from the real execution stream?

## Target
- File/function: common/trace.go: Write
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, reordered chunks
- Exploit idea: send valid-looking chunks in a manipulated order to rewrite visible output
- Invariant to test: stream ordering must be monotonic and tied to one live execution
- Expected Immunefi impact: output tampering or false job evidence
- Fast validation: reorder chunks and verify the stream rejects or reorders safely
