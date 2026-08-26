# Q3575: concurrent submissions break a uniqueness guard in evm_transfer_controller.CreateWithRelayer

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) race two requests through `CreateWithRelayer` at POST /v2/transfers/evm so a uniqueness or single-use guard is defeated (duplicate initiator, duplicate bridge, double run, double transfer)?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: amount and allowHigherAmounts flag (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `amount and allowHigherAmounts flag`.
- Invariant to test: guards must be enforced by a transactional/unique constraint
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: concurrent handler test asserting exactly one success
