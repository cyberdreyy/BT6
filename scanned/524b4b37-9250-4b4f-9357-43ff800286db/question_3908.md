# Q3908: Do headers, auth, or body survive target rebinding

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `Do` preserve headers, auth, or body state after the logical request target changed during retry handling?

## Target
- File/function: network/retry_requester.go: Do
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, late target changes and preserved request state
- Exploit idea: reuse trusted state after the logical request target moved
- Invariant to test: request state must be recomputed whenever the logical target changes
- Expected Immunefi impact: wrong-target transfer or secret-bearing request reuse
- Fast validation: change targets late and verify state is rebound or rejected
