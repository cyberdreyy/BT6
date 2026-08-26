# Q1361: resolver executes before auth on error in auth.authenticateUser

## Question
Does `authenticateUser` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin perform its side effect before its role assertion returns, so an authenticated node user holding only the 'view' role still causes the change while receiving an authorization error?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUser`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `variables` and inspect state afterwards.
- Invariant to test: authorization must complete before any side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test asserting no state change accompanies an authorization error
