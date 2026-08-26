# Q0285: redaction applied only on one route in vault.NewVerifyDKGResultResource

## Question
Is redaction in `NewVerifyDKGResultResource` applied on the index route but not on show/export/create at the JSON:API response of /v2/vault/dkg_results/*, letting an authenticated node user holding only the 'edit' role (non-admin) read the secret through the other route?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewVerifyDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: verify vs export route selection (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare `verify vs export route selection` across all routes rendering the same resource.
- Invariant to test: redaction must be a property of the resource, not of one route
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test comparing the field set across routes
