# Q2213: job spec references another owner's credential in vault_controller.VerifyDKGResult

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) create or update a job through `VerifyDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export that references a bridge, initiator or key belonging to someone else, causing the node to use that credential on the attacker's behalf?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `VerifyDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: dealer/recipient key identifiers (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `dealer/recipient key identifiers` referencing the foreign object by name.
- Invariant to test: specs may only reference objects the submitter is entitled to use
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test submitting a spec referencing a foreign credential
