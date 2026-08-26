# Q5186: pagination parameter injection in gql.GetGQLAuthenticatedSession

## Question
Can an authenticated node user holding only the 'view' role pass a crafted page/size value through `GetGQLAuthenticatedSession` on POST /query (GraphQL) guarded by AuthenticateGQL that reaches the query layer unvalidated and returns rows belonging to other users or unfiltered secret columns?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `GetGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the GraphQL document (query/mutation/alias/fragment) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `GraphQL document (query/mutation/alias/fragment)` with negative, overflowing or non-numeric values.
- Invariant to test: pagination inputs must be validated and never widen the row filter
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over ParsePaginatedRequest with hostile values asserting bounded output
