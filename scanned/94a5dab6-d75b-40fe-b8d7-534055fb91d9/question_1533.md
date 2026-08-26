# Q1533: concurrent submissions break a uniqueness guard in replay_controller.ReplayFromBlock

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) race two requests through `ReplayFromBlock` at POST /v2/replay_from_block/:number so a uniqueness or single-use guard is defeated (duplicate initiator, duplicate bridge, double run, double transfer)?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: evmChainID and force query parameters (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `evmChainID and force query parameters`.
- Invariant to test: guards must be enforced by a transactional/unique constraint
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: concurrent handler test asserting exactly one success
