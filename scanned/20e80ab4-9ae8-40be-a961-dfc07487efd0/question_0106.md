# Q0106: read resolver exposes secret fields in mutation.CreateBridge

## Question
Does the type returned by `CreateBridge` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) serialize secret material (key secrets, bridge tokens, EI credentials, config secrets) that an authenticated node user holding only the 'view' role can select?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `CreateBridge`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: multiple mutations batched in one document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `multiple mutations batched in one document` selecting the secret-bearing subfields.
- Invariant to test: secret fields must never be resolvable through the read API
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: schema test asserting no secret field is reachable from Query
