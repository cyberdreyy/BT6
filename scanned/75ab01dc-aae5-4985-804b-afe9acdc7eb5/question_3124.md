# Q3124: state change without authorization ordering in keys_controller.Create

## Question
Does `Create` at /v2/keys/:keyType Index/Export/Import/Delete routes mutate state before completing its authorization or validation, so an authenticated node user holding only the 'view' role gets the effect together with the error?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the keyType path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `keyType path parameter` that fails late.
- Invariant to test: no state change may precede a completed authorization
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test asserting no mutation accompanies an error response
