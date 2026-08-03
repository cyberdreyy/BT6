# Q3985: regenerateRequestBody retry state leaks across jobs or operations

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `regenerateRequestBody` reuse mutable retry state from one job or operation in another independent operation?

## Target
- File/function: network/retry_requester.go: regenerateRequestBody
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, rapid sequential operations and shared retry state
- Exploit idea: hold mutable retry state too globally across operations
- Invariant to test: retry state must remain isolated per logical operation
- Expected Immunefi impact: cross-job confusion or stale-state reuse
- Fast validation: run sequential operations and verify retry state never leaks forward
