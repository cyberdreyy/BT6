# Q1050: key-creating mutation reachable below role in api_token.NewAPIToken

## Question
Can an authenticated node user holding only the 'view' role create or import a key through `NewAPIToken` at POST /query createAPIToken/deleteAPIToken mutations without admin rights, planting a key the node will later sign with?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `NewAPIToken`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: aliased repeats of the mutation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `aliased repeats of the mutation` with attacker-supplied key material.
- Invariant to test: key material mutations require the admin role
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: resolver test creating/importing keys from low-role sessions
