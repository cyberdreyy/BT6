# Q1307: export bundle rendered to a non-owner in vault.NewExportDKGResultResource

## Question
Does `NewExportDKGResultResource` render exported key material at the JSON:API response of /v2/vault/dkg_results/* to any caller passing the role gate rather than the key owner/admin only?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewExportDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: the DKG result requested (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `DKG result requested` from the weakest role accepted.
- Invariant to test: export material may only be rendered to an admin-authenticated owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test requesting the export from each role
