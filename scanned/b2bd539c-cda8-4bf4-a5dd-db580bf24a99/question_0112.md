# Q0112: object identifier not ownership-scoped in eth_keys_controller.NewETHKeysController

## Question
Can an authenticated node user holding only the 'view' role pass an identifier at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter that makes `NewETHKeysController` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `NewETHKeysController`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export password` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
