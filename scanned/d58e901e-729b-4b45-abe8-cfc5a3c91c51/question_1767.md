# Q1767: response includes other users' objects in replay_controller.ReplayFromBlock

## Question
Does the listing produced by `ReplayFromBlock` at POST /v2/replay_from_block/:number include records outside an authenticated node user holding only the 'edit' role (non-admin)'s entitlement (other users, other initiators, other owners)?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: the block number path parameter (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `block number path parameter` and compare returned ids to the caller's scope.
- Invariant to test: listings must be filtered by the caller's entitlement
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing listing contents across roles
