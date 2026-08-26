# Q0979: state change without authorization ordering in bridge_types_controller.ValidateBridgeTypeNotExist

## Question
Does `ValidateBridgeTypeNotExist` at POST/PATCH/GET /v2/bridge_types mutate state before completing its authorization or validation, so an authenticated node user holding only the 'edit' role (non-admin) gets the effect together with the error?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeTypeNotExist`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `bridge name and URL` that fails late.
- Invariant to test: no state change may precede a completed authorization
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test asserting no mutation accompanies an error response
