# Q0349: import path plants attacker key material in eth_keys_controller.NewETHKeysController

## Question
Can an authenticated node user holding only the 'view' role import key material through `NewETHKeysController` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter so the node later signs oracle reports or transactions with an attacker-known key?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `NewETHKeysController`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: the address path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `address path parameter` containing a key the attacker generated.
- Invariant to test: key import must be admin-only and validated
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test importing a key from a non-admin session
