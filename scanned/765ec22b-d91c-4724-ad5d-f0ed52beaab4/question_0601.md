# Q0601: error/status fields carry raw upstream output in vault.NewVerifyDKGResultResource

## Question
Does `NewVerifyDKGResultResource` include raw upstream errors or task results at the JSON:API response of /v2/vault/dkg_results/* that contain secrets or internal endpoints readable by an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewVerifyDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: verify vs export route selection (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger a failing run then fetch `verify vs export route selection`.
- Invariant to test: rendered errors must be sanitized
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting rendered error fields are sanitized
