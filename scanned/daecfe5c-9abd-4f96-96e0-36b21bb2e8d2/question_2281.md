# Q2281: read route exposes a write-only field in eth_keys_controller.NewETHKeysController

## Question
Does the read path through `NewETHKeysController` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `NewETHKeysController`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `export password` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
