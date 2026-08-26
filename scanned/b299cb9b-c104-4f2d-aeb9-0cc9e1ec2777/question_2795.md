# Q2795: read resolver exposes secret fields in auth.authenticateUserCanEdit

## Question
Does the type returned by `authenticateUserCanEdit` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin serialize secret material (key secrets, bridge tokens, EI credentials, config secrets) that an authenticated node user holding only the 'view' role can select?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserCanEdit`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: aliases and nested selections (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `aliases and nested selections` selecting the secret-bearing subfields.
- Invariant to test: secret fields must never be resolvable through the read API
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: schema test asserting no secret field is reachable from Query
