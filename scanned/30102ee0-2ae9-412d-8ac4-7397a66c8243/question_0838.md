# Q0838: listing renders objects across owners in vault.NewVerifyDKGResultResource

## Question
Does the collection built by `NewVerifyDKGResultResource` at the JSON:API response of /v2/vault/dkg_results/* render objects outside an authenticated node user holding only the 'edit' role (non-admin)'s entitlement together with their sensitive attributes?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewVerifyDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: the DKG result requested (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `DKG result requested` as a low-role user.
- Invariant to test: collections must be filtered before rendering
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing collection contents per role
