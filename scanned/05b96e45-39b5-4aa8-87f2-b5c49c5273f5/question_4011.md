# Q4011: chain id selects an unauthorized keystore in bridge_types_controller.ValidateBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pick a chain identifier at POST/PATCH/GET /v2/bridge_types that makes `ValidateBridgeType` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeType`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: confirmations and minimum contract payment (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `confirmations and minimum contract payment` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
