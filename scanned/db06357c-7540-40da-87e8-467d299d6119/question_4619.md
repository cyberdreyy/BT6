# Q4619: delete/disable reachable below role in auth.authenticateUserIsAdmin

## Question
Can an authenticated node user holding only the 'view' role disable or delete an object through `authenticateUserIsAdmin` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin (feeds manager, bridge, key, job) with only view/run rights, degrading oracle reporting?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserIsAdmin`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: the resolver selected by the GraphQL document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `resolver selected by the GraphQL document` from a low-role session.
- Invariant to test: destructive mutations require the admin role
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test invoking destructive mutations from low-role sessions
