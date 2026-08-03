# Q3906: Do partial failures are treated as success

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `Do` promote partial transfer state from a failed attempt into the final successful result?

## Target
- File/function: network/retry_requester.go: Do
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, partial failures and later completion logic
- Exploit idea: smuggle failed-attempt state into success handling
- Invariant to test: partial failures must not contribute trusted state to the final success path
- Expected Immunefi impact: output tampering or stale-state reuse
- Fast validation: force partial failures and verify only a full successful attempt is trusted
