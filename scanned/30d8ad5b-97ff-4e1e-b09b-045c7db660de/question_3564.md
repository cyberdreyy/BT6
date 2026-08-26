# Q3564: key-creating mutation reachable below role in user.CreatedAt

## Question
Can an authenticated node user holding only the 'view' role create or import a key through `CreatedAt` at POST /query updateUserPassword mutation and user query without admin rights, planting a key the node will later sign with?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `CreatedAt`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: selection set on the User type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `selection set on the User type` with attacker-supplied key material.
- Invariant to test: key material mutations require the admin role
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: resolver test creating/importing keys from low-role sessions
