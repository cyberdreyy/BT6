# Q3129: state change without authorization ordering in loop_registry.discoveryHandler

## Question
Does `discoveryHandler` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) mutate state before completing its authorization or validation, so an authenticated node user holding only the 'view' role gets the effect together with the error?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `discoveryHandler`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: the POST /symbol body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `POST /symbol body` that fails late.
- Invariant to test: no state change may precede a completed authorization
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test asserting no mutation accompanies an error response
