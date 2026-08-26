# Q2285: read route exposes a write-only field in vault_controller.VerifyDKGResult

## Question
Does the read path through `VerifyDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `VerifyDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: the export request parameters (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `export request parameters` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
