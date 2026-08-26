# Q1342: route group ordering in gql.AuthenticateGQL

## Question
Does the registration order around `AuthenticateGQL` place an unauthenticated group after an authenticated one so a path registered twice is served by the unauthenticated handler for an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: operationName and variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `operationName and variables` against paths registered in more than one group.
- Invariant to test: each path may be served by exactly one middleware chain, the most restrictive one
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route-table test asserting no path is registered in both authenticated and unauthenticated groups
