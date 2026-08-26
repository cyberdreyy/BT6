# Q4172: wildcard parameter swallows a route in router.graphqlHandler

## Question
Does a wildcard/param segment on the path to `graphqlHandler` capture a more specific protected route so an unauthenticated HTTP client that can reach the node API port's request at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) is served by a handler with weaker checks?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `graphqlHandler`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: request body JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `request body JSON` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
