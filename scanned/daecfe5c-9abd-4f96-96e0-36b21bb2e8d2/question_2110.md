# Q2110: wildcard parameter swallows a route in api.ParsePaginatedRequest

## Question
Does a wildcard/param segment on the path to `ParsePaginatedRequest` capture a more specific protected route so an authenticated node user holding only the 'view' role's request at page/size query parameters on /v2 index endpoints is served by a handler with weaker checks?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `ParsePaginatedRequest`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `page and size query values` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
