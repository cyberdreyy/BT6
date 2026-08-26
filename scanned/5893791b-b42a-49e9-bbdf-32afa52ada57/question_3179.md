# Q3179: token secret returned on read in auth.authenticateUserCanEdit

## Question
Is the token secret produced by `authenticateUserCanEdit` retrievable again at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin (on query or repeat mutation) so an authenticated node user holding only the 'view' role can read a secret issued to an admin?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserCanEdit`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: aliases and nested selections (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Query `aliases and nested selections` after creation.
- Invariant to test: token secrets must be shown once, at creation, to their owner only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test asserting the secret is absent from all read paths
