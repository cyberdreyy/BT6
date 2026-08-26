# Q0749: resume/callback path unauthenticated or unbound in evm_transfer_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) resume or complete a pending run through `Create` at POST /v2/transfers/evm by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `Create`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: amount and allowHigherAmounts flag (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `amount and allowHigherAmounts flag` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
