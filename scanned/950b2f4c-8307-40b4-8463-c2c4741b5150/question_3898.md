# Q3898: newRetryRequester auth or object selection survives logical target changes

## Question
Can an unprivileged GitLab user or pipeline author enter through job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing and make `newRetryRequester` preserve auth, selected object, or related state after the logical target changed in the same operation family?

## Target
- File/function: network/retry_requester.go: newRetryRequester
- Entrypoint: job trace, cancel/abort, or job-state updates driven by attacker-controlled live job output or reconnect timing
- Attacker controls: trace bytes, offsets, reconnect timing, repeated state transitions, and chunk sizes, logical target changes within one operation family
- Exploit idea: reuse trusted state after the logical target has moved
- Invariant to test: auth and object selection must be rebound whenever the logical target changes
- Expected Immunefi impact: wrong-target mutation or secret-bearing request reuse
- Fast validation: change logical targets late and verify state is recomputed or rejected
