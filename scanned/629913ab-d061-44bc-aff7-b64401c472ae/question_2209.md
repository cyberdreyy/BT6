# Q2209: job spec references another owner's credential in eth_keys_controller.NewETHKeysController

## Question
Can an authenticated node user holding only the 'view' role create or update a job through `NewETHKeysController` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter that references a bridge, initiator or key belonging to someone else, causing the node to use that credential on the attacker's behalf?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `NewETHKeysController`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: the address path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `address path parameter` referencing the foreign object by name.
- Invariant to test: specs may only reference objects the submitter is entitled to use
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test submitting a spec referencing a foreign credential
