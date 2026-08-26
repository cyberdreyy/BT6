# Q0206: custom marshaller leaks on error in vault.NewVerifyDKGResultResource

## Question
Does the marshalling path around `NewVerifyDKGResultResource` fall back to default struct marshalling on error at the JSON:API response of /v2/vault/dkg_results/*, exposing unredacted fields to an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewVerifyDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: the DKG result requested (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch via `DKG result requested`.
- Invariant to test: marshalling failure must produce an error, never a raw dump
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test forcing marshal errors and asserting no raw payload
