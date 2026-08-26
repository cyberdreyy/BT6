# Q4926: balance/limit guard bypass in evm_transfer_controller.CreateEVMLegacy

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) bypass the balance or limit validation performed by `CreateEVMLegacy` at POST /v2/transfers/evm through numeric parsing (overflow, negative, scientific notation, unit confusion)?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateEVMLegacy`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: gas limit and token contract fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `gas limit and token contract fields` in hostile numeric encodings.
- Invariant to test: amount validation must be exact over the canonical big-int representation
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over the amount parser and balance validator
