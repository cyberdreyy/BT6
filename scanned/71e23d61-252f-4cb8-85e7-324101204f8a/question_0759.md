# Q0759: resource type confusion in vault.NewVerifyDKGResultResource

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) cause `NewVerifyDKGResultResource` at the JSON:API response of /v2/vault/dkg_results/* to render one resource type with another's attribute set, exposing fields the intended presenter would redact?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewVerifyDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: verify vs export route selection (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `verify vs export route selection` with a mismatched type/id.
- Invariant to test: the presenter selected must match the object type exactly
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over presenter selection for mismatched types
