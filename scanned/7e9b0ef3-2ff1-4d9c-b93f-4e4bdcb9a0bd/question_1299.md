# Q1299: plugin sub-path proxying in replay_controller.ReplayFromBlock

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) reach an unintended plugin endpoint through the path segment handled by `ReplayFromBlock` at POST /v2/replay_from_block/:number, obtaining plugin debug data, memory dumps or command lines with secrets?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: the block number path parameter (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `block number path parameter` with crafted plugin name and sub-path.
- Invariant to test: plugin routes must expose only an explicit allowlist of endpoints, admin-gated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test enumerating plugin sub-paths from a low-role session
