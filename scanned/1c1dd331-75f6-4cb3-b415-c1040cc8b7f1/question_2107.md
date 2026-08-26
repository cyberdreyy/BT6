# Q2107: wildcard parameter swallows a route in gql.AuthenticateGQL

## Question
Does a wildcard/param segment on the path to `AuthenticateGQL` capture a more specific protected route so an authenticated node user holding only the 'view' role's request at POST /query (GraphQL) guarded by AuthenticateGQL is served by a handler with weaker checks?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: batched operations in one request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `batched operations in one request` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
