# Q1111: authorization oracle via response differences in api.ParsePaginatedRequest

## Question
Do the headers/status produced by `ParsePaginatedRequest` differ enough between 'no such object' and 'forbidden' on page/size query parameters on /v2 index endpoints to let an authenticated node user holding only the 'view' role enumerate protected objects before escalating?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `ParsePaginatedRequest`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: JSON:API document fields in the request body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare responses for `JSON:API document fields in the request body` across existing and non-existing identifiers.
- Invariant to test: authorization failures must be indistinguishable from missing objects
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting identical status/body for forbidden and missing resources
