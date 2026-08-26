# Q1293: plugin sub-path proxying in eth_keys_controller.NewETHKeysController

## Question
Can an authenticated node user holding only the 'view' role reach an unintended plugin endpoint through the path segment handled by `NewETHKeysController` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter, obtaining plugin debug data, memory dumps or command lines with secrets?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `NewETHKeysController`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: the address path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `address path parameter` with crafted plugin name and sub-path.
- Invariant to test: plugin routes must expose only an explicit allowlist of endpoints, admin-gated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test enumerating plugin sub-paths from a low-role session
