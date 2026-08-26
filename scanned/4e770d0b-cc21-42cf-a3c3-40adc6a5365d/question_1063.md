# Q1063: replay/reprocess trigger under-gated in vault_controller.VerifyDKGResult

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) force reprocessing of chain history through `VerifyDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export so the node re-emits or re-reports data derived from a range the attacker chose?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `VerifyDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: dealer/recipient key identifiers (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `dealer/recipient key identifiers` with a crafted block range/chain id.
- Invariant to test: reprocessing must be admin-gated and range-validated
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test invoking the replay route from a low-role session
