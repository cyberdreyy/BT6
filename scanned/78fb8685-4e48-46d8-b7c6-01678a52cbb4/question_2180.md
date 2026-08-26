# Q2180: double decoding of identifiers in gql.AuthenticateGQL

## Question
Is an identifier decoded twice between the authorization check and the lookup on the path through `AuthenticateGQL`, letting an authenticated node user holding only the 'view' role authorize one object at POST /query (GraphQL) guarded by AuthenticateGQL and act on another?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the GraphQL document (query/mutation/alias/fragment) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `GraphQL document (query/mutation/alias/fragment)` percent-encoded so the two stages resolve to different values.
- Invariant to test: the value authorized and the value used must be byte-identical
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the authorized identifier equals the identifier passed to the store
