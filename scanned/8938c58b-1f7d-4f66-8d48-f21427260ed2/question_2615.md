# Q2615: import path plants attacker key material in vault_controller.ExportDKGResult

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) import key material through `ExportDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export so the node later signs oracle reports or transactions with an attacker-known key?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `ExportDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: dealer/recipient key identifiers (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `dealer/recipient key identifiers` containing a key the attacker generated.
- Invariant to test: key import must be admin-only and validated
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test importing a key from a non-admin session
