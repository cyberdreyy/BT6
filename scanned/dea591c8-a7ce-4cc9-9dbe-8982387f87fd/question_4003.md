# Q4003: aliased repeats bypass a single-shot guard in auth.authenticateUserIsAdmin

## Question
Can an authenticated node user holding only the 'view' role use aliases at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin to invoke `authenticateUserIsAdmin` many times in one document, defeating a per-request guard, quota or single-use check?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserIsAdmin`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `variables` with N aliased copies.
- Invariant to test: per-request guards must count executions, not documents
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: resolver test posting an aliased document and counting executions
