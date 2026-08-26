# Q4178: wildcard parameter swallows a route in helpers.paginatedResponse

## Question
Does a wildcard/param segment on the path to `paginatedResponse` capture a more specific protected route so an authenticated node user holding only the 'view' role's request at the JSON:API response writer used by every /v2 controller is served by a handler with weaker checks?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: pagination parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `pagination parameters` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
