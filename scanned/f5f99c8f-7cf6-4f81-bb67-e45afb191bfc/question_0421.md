# Q0421: error extensions leak internals in query.Bridge

## Question
Do the error extensions produced by `Bridge` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) carry stack traces, SQL, DSNs or key identifiers useful to an authenticated node user holding only the 'view' role for follow-on key theft?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Bridge`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: pagination arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `pagination arguments`.
- Invariant to test: GraphQL errors must expose no server internals
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test asserting error extensions match an allowlist
