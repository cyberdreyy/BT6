# Q5414: metrics token comparison in gql.GetGQLAuthenticatedSession

## Question
Can an authenticated node user holding only the 'view' role authenticate to the metrics endpoint gated near `GetGQLAuthenticatedSession` by exploiting a weak or non-constant-time token comparison, obtaining node internals used to plan key theft?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `GetGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the GraphQL document (query/mutation/alias/fragment) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Probe `GraphQL document (query/mutation/alias/fragment)` with prefix-varied tokens.
- Invariant to test: metrics auth must use constant-time comparison of the full token
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test on the metrics auth helper with near-miss tokens
