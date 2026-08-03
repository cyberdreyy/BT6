# Q3996: regenerateRequestBody stale normalized target survives into the next job

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `regenerateRequestBody` keep normalized target or retry state alive into a later job on the same runner process?

## Target
- File/function: network/retry_requester.go: regenerateRequestBody
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, rapid sequential jobs and shared target state
- Exploit idea: persist target-binding state beyond one job lifetime
- Invariant to test: retry target state must terminate with the logical job operation
- Expected Immunefi impact: cross-job confusion or stale-state reuse
- Fast validation: run rapid sequential jobs and verify retry target state does not leak forward
