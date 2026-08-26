# Q2936: resume/callback path unauthenticated or unbound in evm_transfer_controller.CreateWithRelayer

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) resume or complete a pending run through `CreateWithRelayer` at POST /v2/transfers/evm by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: gas limit and token contract fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `gas limit and token contract fields` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
