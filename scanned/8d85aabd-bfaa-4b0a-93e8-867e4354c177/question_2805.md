# Q2805: run triggered on a job the caller cannot access in eth_keys_controller.createETHKeyResource

## Question
Can an authenticated node user holding only the 'view' role trigger execution through `createETHKeyResource` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter for a job they were not granted, injecting attacker-chosen input into an oracle report?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `createETHKeyResource`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export password` naming another job's id with an attacker payload.
- Invariant to test: run triggering must be bound to the caller's entitlement for that exact job
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job and asserting rejection
