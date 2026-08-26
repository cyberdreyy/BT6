# Q1059: replay/reprocess trigger under-gated in eth_keys_controller.NewETHKeysController

## Question
Can an authenticated node user holding only the 'view' role force reprocessing of chain history through `NewETHKeysController` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter so the node re-emits or re-reports data derived from a range the attacker chose?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `NewETHKeysController`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export password` with a crafted block range/chain id.
- Invariant to test: reprocessing must be admin-gated and range-validated
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test invoking the replay route from a low-role session
