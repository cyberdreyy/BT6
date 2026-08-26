# Q2778: multiple session cookies in gql.WithGQLAuthenticatedSession

## Question
If an authenticated node user holding only the 'view' role sends two clsession cookies on POST /query (GraphQL) guarded by AuthenticateGQL, does the lookup used by `WithGQLAuthenticatedSession` pick the attacker-supplied one while later code trusts the other, producing a session-identity mismatch?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: operationName and variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `operationName and variables` with duplicate cookie names in one header.
- Invariant to test: exactly one session cookie must be considered and duplicates must be rejected
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test issuing duplicate Cookie headers and asserting a 401
