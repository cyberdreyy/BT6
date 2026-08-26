# Q1922: privileged default applied on missing field in evm_transfer_controller.Create

## Question
Does an omitted field in an authenticated node user holding only the 'edit' role (non-admin)'s request cause `Create` at POST /v2/transfers/evm to apply a permissive default (all chains, no limit, enabled, admin) rather than rejecting?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `Create`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: from/to addresses (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Omit `from/to addresses` from the request body.
- Invariant to test: missing security-relevant fields must be rejected, not defaulted permissively
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test omitting each security-relevant field
