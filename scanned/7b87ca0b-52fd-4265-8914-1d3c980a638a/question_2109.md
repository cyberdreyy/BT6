# Q2109: wildcard parameter swallows a route in cookies.FindSessionCookie

## Question
Does a wildcard/param segment on the path to `FindSessionCookie` capture a more specific protected route so an unauthenticated HTTP client that can reach the node API port's request at the Cookie header on any authenticated /v2 route is served by a handler with weaker checks?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: multiple clsession cookies in one header (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `multiple clsession cookies in one header` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
