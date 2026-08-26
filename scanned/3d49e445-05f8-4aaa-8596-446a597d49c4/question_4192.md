# Q4192: error extensions leak internals in auth.authenticateUserIsAdmin

## Question
Do the error extensions produced by `authenticateUserIsAdmin` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin carry stack traces, SQL, DSNs or key identifiers useful to an authenticated node user holding only the 'view' role for follow-on key theft?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserIsAdmin`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `variables`.
- Invariant to test: GraphQL errors must expose no server internals
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test asserting error extensions match an allowlist
