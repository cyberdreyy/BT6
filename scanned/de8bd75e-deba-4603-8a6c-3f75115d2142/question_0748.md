# Q0748: resume/callback path unauthenticated or unbound in vault_controller.VerifyDKGResult

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) resume or complete a pending run through `VerifyDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `VerifyDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: the DKG result payload (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `DKG result payload` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
