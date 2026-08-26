# Q1683: deletion enables silent takeover in eth_keys_controller.NewETHKeysController

## Question
Can an authenticated node user holding only the 'view' role delete or disable an object through `NewETHKeysController` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter and recreate it with attacker-controlled contents under the same name, so existing jobs silently use it?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `NewETHKeysController`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Delete then recreate via `export password`.
- Invariant to test: recreation must not inherit references from a deleted object without revalidation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test deleting and recreating a referenced object
