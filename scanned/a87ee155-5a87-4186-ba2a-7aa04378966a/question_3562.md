# Q3562: key-creating mutation reachable below role in auth.authenticateUserCanEdit

## Question
Can an authenticated node user holding only the 'view' role create or import a key through `authenticateUserCanEdit` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin without admin rights, planting a key the node will later sign with?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserCanEdit`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: aliases and nested selections (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `aliases and nested selections` with attacker-supplied key material.
- Invariant to test: key material mutations require the admin role
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: resolver test creating/importing keys from low-role sessions
