# Q1284: pagination arguments widen the scope in api_token.NewAPIToken

## Question
Can an authenticated node user holding only the 'view' role pass pagination arguments to `NewAPIToken` at POST /query createAPIToken/deleteAPIToken mutations that overflow into an unfiltered query returning other owners' rows?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `NewAPIToken`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: aliased repeats of the mutation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `aliased repeats of the mutation` with negative/overflowing values.
- Invariant to test: pagination must be clamped and never widen filters
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over pagination arguments
