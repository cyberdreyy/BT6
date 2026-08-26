# Q2106: wildcard parameter swallows a route in auth.AuthenticateBySession

## Question
Does a wildcard/param segment on the path to `AuthenticateBySession` capture a more specific protected route so a holder of a restricted API access-key/secret pair's request at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list is served by a handler with weaker checks?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateBySession`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the target route and role wrapper reached (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `target route and role wrapper reached` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
