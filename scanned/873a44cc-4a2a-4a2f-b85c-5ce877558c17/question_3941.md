# Q3941: read resolver exposes secret fields in api_token.NewCreateAPITokenPayload

## Question
Does the type returned by `NewCreateAPITokenPayload` at POST /query createAPIToken/deleteAPIToken mutations serialize secret material (key secrets, bridge tokens, EI credentials, config secrets) that an authenticated node user holding only the 'view' role can select?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `NewCreateAPITokenPayload`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: aliased repeats of the mutation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `aliased repeats of the mutation` selecting the secret-bearing subfields.
- Invariant to test: secret fields must never be resolvable through the read API
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: schema test asserting no secret field is reachable from Query
