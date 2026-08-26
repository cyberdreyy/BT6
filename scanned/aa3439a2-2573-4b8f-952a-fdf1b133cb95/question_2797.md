# Q2797: read resolver exposes secret fields in user.CreatedAt

## Question
Does the type returned by `CreatedAt` at POST /query updateUserPassword mutation and user query serialize secret material (key secrets, bridge tokens, EI credentials, config secrets) that an authenticated node user holding only the 'view' role can select?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `CreatedAt`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: selection set on the User type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `selection set on the User type` selecting the secret-bearing subfields.
- Invariant to test: secret fields must never be resolvable through the read API
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: schema test asserting no secret field is reachable from Query
