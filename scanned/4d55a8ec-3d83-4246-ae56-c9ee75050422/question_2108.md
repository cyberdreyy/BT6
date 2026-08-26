# Q2108: wildcard parameter swallows a route in helpers.jsonAPIError

## Question
Does a wildcard/param segment on the path to `jsonAPIError` capture a more specific protected route so an unauthenticated HTTP client that can reach the node API port's request at any /v2 or /query error response path is served by a handler with weaker checks?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: inputs that force an error branch (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `inputs that force an error branch` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
