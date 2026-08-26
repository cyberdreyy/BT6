# Q1833: error extensions leak internals in mutation.CreateCSAKey

## Question
Do the error extensions produced by `CreateCSAKey` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) carry stack traces, SQL, DSNs or key identifiers useful to an authenticated node user holding only the 'view' role for follow-on key theft?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `CreateCSAKey`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: id arguments referencing other users' objects (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `id arguments referencing other users' objects`.
- Invariant to test: GraphQL errors must expose no server internals
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test asserting error extensions match an allowlist
