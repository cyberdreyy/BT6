# Q5302: authorization oracle via response differences in helpers.paginatedRequest

## Question
Do the headers/status produced by `paginatedRequest` differ enough between 'no such object' and 'forbidden' on the JSON:API response writer used by every /v2 controller to let an authenticated node user holding only the 'view' role enumerate protected objects before escalating?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedRequest`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: inputs that select the error branch (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare responses for `inputs that select the error branch` across existing and non-existing identifiers.
- Invariant to test: authorization failures must be indistinguishable from missing objects
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting identical status/body for forbidden and missing resources
