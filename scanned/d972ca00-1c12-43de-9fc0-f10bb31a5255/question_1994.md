# Q1994: error text discloses key or file paths in vault_controller.VerifyDKGResult

## Question
Do errors from `VerifyDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export reveal keystore paths, key ids, addresses or DB structure that let an authenticated node user holding only the 'edit' role (non-admin) target the next step of a key-theft chain?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `VerifyDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: dealer/recipient key identifiers (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `dealer/recipient key identifiers`.
- Invariant to test: errors must not disclose key identities or filesystem layout
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies exclude paths and key ids
