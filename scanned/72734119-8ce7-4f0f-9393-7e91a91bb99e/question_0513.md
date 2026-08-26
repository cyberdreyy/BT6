# Q0513: spec fields reach outbound requests with node credentials in replay_controller.ReplayFromBlock

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) point a URL/host field accepted by `ReplayFromBlock` at POST /v2/replay_from_block/:number at an internal address or attacker host so the node performs a request carrying its own credentials or secrets?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: the block number path parameter (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `block number path parameter` with an internal or attacker URL.
- Invariant to test: outbound targets from user-supplied specs must be validated and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the URL validator with internal/attacker targets
