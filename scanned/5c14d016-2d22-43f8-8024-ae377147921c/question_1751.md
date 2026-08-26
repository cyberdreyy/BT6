# Q1751: id argument not ownership-checked in auth.authenticateUserCanRun

## Question
Can an authenticated node user holding only the 'view' role pass an identifier for another user's object into `authenticateUserCanRun` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin and read or mutate it because only authentication, not ownership, is verified?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserCanRun`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: aliases and nested selections (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `aliases and nested selections` with an id belonging to another owner.
- Invariant to test: object access must verify ownership/scope in addition to role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test using another owner's id and asserting rejection
