# Q3917: Do retry continues after the owning provider ended

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `Do` continue retrying and mutating state after the owning logical provider or job should have ended?

## Target
- File/function: network/retry_requester.go: Do
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, late retries after provider end
- Exploit idea: let retries outlive the operation that authorized them
- Invariant to test: retry loops must terminate with the owning logical provider
- Expected Immunefi impact: post-job mutation or stale-state hijack
- Fast validation: end the owning operation and verify retries stop immediately
