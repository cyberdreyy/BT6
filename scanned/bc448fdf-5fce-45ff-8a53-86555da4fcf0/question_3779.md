# Q3779: Fail chunk and length mismatches splice data

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `Fail` accept chunk-size or content-length mismatches that splice attacker-chosen and trusted data together?

## Target
- File/function: common/trace.go: Fail
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, mismatched chunk and length metadata
- Exploit idea: trick framing logic into combining bytes from different logical positions
- Invariant to test: framing metadata must preserve exact byte ownership and order
- Expected Immunefi impact: artifact or trace tampering
- Fast validation: use mismatched chunk metadata and verify transfer framing rejects it
