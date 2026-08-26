# Q4424: index route serves privileged payload in gql.WithGQLAuthenticatedSession

## Question
Can an authenticated node user holding only the 'view' role obtain configuration, feature flags or identity data embedded by `WithGQLAuthenticatedSession` into the index/asset response at POST /query (GraphQL) guarded by AuthenticateGQL without authenticating?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: batched operations in one request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `batched operations in one request` anonymously and inspect the served document.
- Invariant to test: unauthenticated responses must contain no node configuration or identity data
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test fetching index/static routes anonymously and asserting a fixed payload
