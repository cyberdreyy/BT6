# Q1853: secret in relationship/included documents in vault.NewExportDKGResultResource

## Question
Does the JSON:API relationship or included section produced around `NewExportDKGResultResource` at the JSON:API response of /v2/vault/dkg_results/* carry secret attributes of related objects to an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewExportDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: verify vs export route selection (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `verify vs export route selection` with include parameters.
- Invariant to test: included resources must be redacted like primary resources
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting included documents pass the same redaction
