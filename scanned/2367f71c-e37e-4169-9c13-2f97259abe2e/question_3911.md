# Q3911: Do retry on stale response mutates the wrong object

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `Do` retry using state derived from a stale response even though the logical object has changed?

## Target
- File/function: network/retry_requester.go: Do
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, stale responses and changed objects
- Exploit idea: treat a stale response as authority for a new logical object
- Invariant to test: retry decisions must remain bound to the exact object that produced the response
- Expected Immunefi impact: wrong-target mutation or stale-object trust
- Fast validation: change objects between attempts and verify stale responses are rejected
