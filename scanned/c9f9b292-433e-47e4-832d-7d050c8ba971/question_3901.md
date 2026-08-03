# Q3901: Do body is reused after the logical target changes

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `Do` reuse a request body prepared for one logical target after a later retry changed the target?

## Target
- File/function: network/retry_requester.go: Do
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, target changes between retries
- Exploit idea: carry stale body state across logical target changes
- Invariant to test: retried bodies must remain bound to one logical target
- Expected Immunefi impact: wrong-target transfer or stale-state reuse
- Fast validation: change the logical target between retries and verify the body is rebound or rejected
