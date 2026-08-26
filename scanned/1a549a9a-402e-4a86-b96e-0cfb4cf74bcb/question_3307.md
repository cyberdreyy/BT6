# Q3307: introspection maps privileged surface in auth.authenticateUserCanEdit

## Question
Can an authenticated node user holding only the 'view' role use introspection at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin to enumerate the mutations guarded near `authenticateUserCanEdit` and their inputs, then probe for the weakest one?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserCanEdit`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: the resolver selected by the GraphQL document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Introspect with `resolver selected by the GraphQL document` and enumerate privileged fields.
- Invariant to test: if introspection is exposed, every field it reveals must still enforce its own role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting introspection-listed mutations all reject view-role sessions
