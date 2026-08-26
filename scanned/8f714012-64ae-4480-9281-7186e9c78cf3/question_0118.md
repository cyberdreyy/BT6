# Q0118: object identifier not ownership-scoped in replay_controller.ReplayFromBlock

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pass an identifier at POST /v2/replay_from_block/:number that makes `ReplayFromBlock` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: evmChainID and force query parameters (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `evmChainID and force query parameters` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
