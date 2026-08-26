# Q0197: secret returned in the success response in replay_controller.ReplayFromBlock

## Question
Does the response produced by `ReplayFromBlock` at POST /v2/replay_from_block/:number include key material, export bundles, passwords, tokens or bridge/EI secrets readable by an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: the block number path parameter (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `block number path parameter` and inspect every field of the response.
- Invariant to test: responses must never carry secret material to a non-owner or low-role caller
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the response body matches a redacted golden fixture
