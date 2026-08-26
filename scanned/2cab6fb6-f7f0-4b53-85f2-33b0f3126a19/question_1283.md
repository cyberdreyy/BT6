# Q1283: pagination arguments widen the scope in auth.authenticateUser

## Question
Can an authenticated node user holding only the 'view' role pass pagination arguments to `authenticateUser` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin that overflow into an unfiltered query returning other owners' rows?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUser`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: aliases and nested selections (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `aliases and nested selections` with negative/overflowing values.
- Invariant to test: pagination must be clamped and never widen filters
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over pagination arguments
