# Q3000: transfer parameters under-validated in evm_transfer_controller.CreateWithRelayer

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) cause `CreateWithRelayer` at POST /v2/transfers/evm to send funds from a node-held key by controlling destination, amount, chain or balance-check flags?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: from/to addresses (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `from/to addresses` with an attacker destination and a flag that skips the balance guard.
- Invariant to test: value transfers require admin authority and must validate destination, amount and chain
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test submitting a transfer from a non-admin session
