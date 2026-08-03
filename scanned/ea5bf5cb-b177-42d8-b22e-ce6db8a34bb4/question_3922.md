# Q3922: executeRequestWithRetries normalized URIs collapse distinct retry targets

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `executeRequestWithRetries` treat two distinct retry targets as equivalent after URI normalization?

## Target
- File/function: network/retry_requester.go: executeRequestWithRetries
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, equivalent-looking URIs or paths
- Exploit idea: collapse distinct logical targets through URI normalization
- Invariant to test: URI normalization must not merge distinct logical targets
- Expected Immunefi impact: wrong-target transfer or stale-state trust
- Fast validation: exercise equivalent-looking URIs and verify retry target binding stays exact
