# Q1109: authorization oracle via response differences in helpers.jsonAPIError

## Question
Do the headers/status produced by `jsonAPIError` differ enough between 'no such object' and 'forbidden' on any /v2 or /query error response path to let an unauthenticated HTTP client that can reach the node API port enumerate protected objects before escalating?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare responses for `unknown IDs and type parameters` across existing and non-existing identifiers.
- Invariant to test: authorization failures must be indistinguishable from missing objects
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting identical status/body for forbidden and missing resources
