# Q3935: executeRequestWithRetries one request object is reused for another target

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `executeRequestWithRetries` reuse the same mutable request object across multiple logical targets?

## Target
- File/function: network/retry_requester.go: executeRequestWithRetries
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, shared request objects and changing targets
- Exploit idea: keep request state alive across distinct targets
- Invariant to test: request objects must not be reused across distinct logical targets
- Expected Immunefi impact: wrong-target transfer or cross-operation confusion
- Fast validation: force target changes across request reuse and verify isolation
