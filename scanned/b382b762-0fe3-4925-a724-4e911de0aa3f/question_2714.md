# Q2714: non-constant-time credential comparison in gql.WithGQLAuthenticatedSession

## Question
Does the credential comparison reached by `WithGQLAuthenticatedSession` from POST /query (GraphQL) guarded by AuthenticateGQL short-circuit on the first differing byte, letting an authenticated node user holding only the 'view' role recover a valid API/EI secret by measuring response timing across requests?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the GraphQL document (query/mutation/alias/fragment) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send many requests varying `GraphQL document (query/mutation/alias/fragment)` one byte at a time and rank by latency.
- Invariant to test: all secret comparisons must be constant time over the full secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: benchmark/timing test over the comparison helper with prefix-matching secrets
