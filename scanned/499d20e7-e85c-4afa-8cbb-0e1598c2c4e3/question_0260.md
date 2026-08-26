# Q0260: nested selection reaches privileged sibling in auth.authenticateUser

## Question
Can an authenticated node user holding only the 'view' role reach a privileged type from an unprivileged root through nested selections resolved by `authenticateUser` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin, since only the root field carries the role check?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUser`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: the resolver selected by the GraphQL document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Traverse `resolver selected by the GraphQL document` into the privileged child type.
- Invariant to test: authorization must be enforced on each resolver, not only on root fields
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test traversing from an allowed root into privileged children
