# Q1498: empty or absent credential accepted in gql.AuthenticateGQL

## Question
Does `AuthenticateGQL` treat an empty access key, empty secret or empty session id presented at POST /query (GraphQL) guarded by AuthenticateGQL as a match against an unset/zero stored value, authenticating an authenticated node user holding only the 'view' role as a real identity?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: batched operations in one request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `batched operations in one request` with empty or omitted credential fields.
- Invariant to test: empty credentials must always fail authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/absent credential fields asserting 401
