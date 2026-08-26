# Q3290: chain selector reaches unintended relayer in gql.WithGQLAuthenticatedSession

## Question
Can an authenticated node user holding only the 'view' role supply a chain identifier through `WithGQLAuthenticatedSession` at POST /query (GraphQL) guarded by AuthenticateGQL that resolves to a relayer/keystore other than the one authorization was evaluated against?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: operationName and variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `operationName and variables` with alternate encodings of the chain id (leading zeros, whitespace, different base).
- Invariant to test: the chain resolved for execution must be the exact chain authorized for the request
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over getChain with equivalent-but-different chain id strings
