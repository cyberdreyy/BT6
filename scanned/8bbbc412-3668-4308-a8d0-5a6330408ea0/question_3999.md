# Q3999: regenerateRequestBody transport state is shared across retry families

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `regenerateRequestBody` reuse transport or retry state between different logical retry families in one runner process?

## Target
- File/function: network/retry_requester.go: regenerateRequestBody
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, multiple retry families and shared transport state
- Exploit idea: hold mutable transport state too globally
- Invariant to test: transport state must remain isolated per logical retry family
- Expected Immunefi impact: cross-operation confusion or stale-state trust
- Fast validation: run different retry families in sequence and verify no shared mutable state
