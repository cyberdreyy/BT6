# Q5780: chain id selects an unauthorized keystore in evm_transfer_controller.CreateEVMLegacy

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pick a chain identifier at POST /v2/transfers/evm that makes `CreateEVMLegacy` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateEVMLegacy`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: evmChainID (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `evmChainID` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
