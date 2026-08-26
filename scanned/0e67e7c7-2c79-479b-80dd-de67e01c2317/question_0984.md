# Q0984: state change without authorization ordering in workflow_keys_controller.Index

## Question
Does `Index` at GET /v2/keys/workflow mutate state before completing its authorization or validation, so an authenticated node user holding only the 'view' role gets the effect together with the error?

## Target
- File/function: [core/web/workflow_keys_controller.go](core/web/workflow_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/workflow
- Attacker controls: the request path and query parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `request path and query parameters` that fails late.
- Invariant to test: no state change may precede a completed authorization
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test asserting no mutation accompanies an error response
