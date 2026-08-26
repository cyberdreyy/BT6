# Q3754: pagination arguments widen the scope in query.Chain

## Question
Can an authenticated node user holding only the 'view' role pass pagination arguments to `Chain` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) that overflow into an unfiltered query returning other owners' rows?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Chain`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: nested selection into key/secret-bearing types (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `nested selection into key/secret-bearing types` with negative/overflowing values.
- Invariant to test: pagination must be clamped and never widen filters
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over pagination arguments
