# Q0987: state change without authorization ordering in replay_controller.ReplayFromBlock

## Question
Does `ReplayFromBlock` at POST /v2/replay_from_block/:number mutate state before completing its authorization or validation, so an authenticated node user holding only the 'edit' role (non-admin) gets the effect together with the error?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: the block number path parameter (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `block number path parameter` that fails late.
- Invariant to test: no state change may precede a completed authorization
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test asserting no mutation accompanies an error response
