# Q2798: read resolver exposes secret fields in query.Chain

## Question
Does the type returned by `Chain` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) serialize secret material (key secrets, bridge tokens, EI credentials, config secrets) that an authenticated node user holding only the 'view' role can select?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Chain`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: nested selection into key/secret-bearing types (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `nested selection into key/secret-bearing types` selecting the secret-bearing subfields.
- Invariant to test: secret fields must never be resolvable through the read API
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: schema test asserting no secret field is reachable from Query
