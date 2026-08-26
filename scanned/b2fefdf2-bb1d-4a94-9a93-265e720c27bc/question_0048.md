# Q0048: secret field serialized in vault.NewVerifyDKGResultResource

## Question
Does the resource built by `NewVerifyDKGResultResource` for the JSON:API response of /v2/vault/dkg_results/* include a secret field (private key, seed, token, password, DSN, share) that an authenticated node user holding only the 'edit' role (non-admin) can read?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewVerifyDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: the DKG result requested (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `DKG result requested` and inspect the JSON:API attributes.
- Invariant to test: presenters must whitelist non-secret attributes only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test over the presenter output
