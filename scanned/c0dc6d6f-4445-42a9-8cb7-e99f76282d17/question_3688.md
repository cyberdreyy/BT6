# Q3688: resolver ignores soft-deleted state in auth.authenticateUserCanEdit

## Question
Does `authenticateUserCanEdit` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin resolve objects that are deleted/disabled, letting an authenticated node user holding only the 'view' role act through a decommissioned bridge, key or manager?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserCanEdit`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: the resolver selected by the GraphQL document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reference `resolver selected by the GraphQL document` for a deleted object.
- Invariant to test: resolvers must filter out deleted/disabled records
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test referencing deleted objects
