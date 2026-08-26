# Q3877: mutation missing its role assertion in auth.authenticateUserIsAdmin

## Question
Can an authenticated node user holding only the 'view' role execute the state-changing resolver `authenticateUserIsAdmin` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin because the resolver body omits authenticateUserCanEdit/IsAdmin while sibling resolvers include it?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserIsAdmin`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: the resolver selected by the GraphQL document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Select the resolver with `resolver selected by the GraphQL document` from a view-role session.
- Invariant to test: every mutation must call the role assertion before touching state
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with view-role sessions
