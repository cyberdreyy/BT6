# Q4015: chain id selects an unauthorized keystore in vault_controller.ExportDKGResult

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pick a chain identifier at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export that makes `ExportDKGResult` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `ExportDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: the export request parameters (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export request parameters` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
