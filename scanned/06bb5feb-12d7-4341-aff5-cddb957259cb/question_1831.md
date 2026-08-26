# Q1831: error extensions leak internals in user.Email

## Question
Do the error extensions produced by `Email` at POST /query updateUserPassword mutation and user query carry stack traces, SQL, DSNs or key identifiers useful to an authenticated node user holding only the 'view' role for follow-on key theft?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `Email`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: selection set on the User type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `selection set on the User type`.
- Invariant to test: GraphQL errors must expose no server internals
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test asserting error extensions match an allowlist
