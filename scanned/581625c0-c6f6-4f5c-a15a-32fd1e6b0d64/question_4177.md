# Q4177: wildcard parameter swallows a route in api.paginationLink

## Question
Does a wildcard/param segment on the path to `paginationLink` capture a more specific protected route so an authenticated node user holding only the 'view' role's request at page/size query parameters on /v2 index endpoints is served by a handler with weaker checks?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: JSON:API document fields in the request body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `JSON:API document fields in the request body` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
