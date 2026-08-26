# Q5326: identifier-to-object confusion across types in evm_transfer_controller.CreateEVMLegacy

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) supply an identifier of the wrong type/namespace at POST /v2/transfers/evm so `CreateEVMLegacy` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateEVMLegacy`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: evmChainID (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `evmChainID` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
