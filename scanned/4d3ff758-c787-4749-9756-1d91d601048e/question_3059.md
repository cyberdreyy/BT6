# Q3059: balance/limit guard bypass in bridge_types_controller.ValidateBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) bypass the balance or limit validation performed by `ValidateBridgeType` at POST/PATCH/GET /v2/bridge_types through numeric parsing (overflow, negative, scientific notation, unit confusion)?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeType`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: confirmations and minimum contract payment (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `confirmations and minimum contract payment` in hostile numeric encodings.
- Invariant to test: amount validation must be exact over the canonical big-int representation
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over the amount parser and balance validator
