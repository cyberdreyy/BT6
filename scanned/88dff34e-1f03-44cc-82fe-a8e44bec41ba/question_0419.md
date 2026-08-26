# Q0419: error extensions leak internals in api_token.NewAPIToken

## Question
Do the error extensions produced by `NewAPIToken` at POST /query createAPIToken/deleteAPIToken mutations carry stack traces, SQL, DSNs or key identifiers useful to an authenticated node user holding only the 'view' role for follow-on key theft?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `NewAPIToken`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the returned token fields selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `returned token fields selected`.
- Invariant to test: GraphQL errors must expose no server internals
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test asserting error extensions match an allowlist
