# Q0443: identifier reveals sensitive identity in vault.NewVerifyDKGResultResource

## Question
Does the identifier or metadata rendered by `NewVerifyDKGResultResource` at the JSON:API response of /v2/vault/dkg_results/* reveal key identities, addresses or credential fingerprints that let an authenticated node user holding only the 'edit' role (non-admin) target key theft or fund movement?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewVerifyDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: verify vs export route selection (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `verify vs export route selection` at the lowest role.
- Invariant to test: identity metadata must be limited to what the caller's role needs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing rendered identifiers per role
