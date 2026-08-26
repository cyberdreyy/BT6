# Q0591: run triggered on a job the caller cannot access in evm_transfer_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) trigger execution through `Create` at POST /v2/transfers/evm for a job they were not granted, injecting attacker-chosen input into an oracle report?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `Create`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: gas limit and token contract fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `gas limit and token contract fields` naming another job's id with an attacker payload.
- Invariant to test: run triggering must be bound to the caller's entitlement for that exact job
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job and asserting rejection
