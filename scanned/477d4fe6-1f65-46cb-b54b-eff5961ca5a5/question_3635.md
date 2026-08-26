# Q3635: update path widens privileges of an existing object in eth_keys_controller.createETHKeyResource

## Question
Can an authenticated node user holding only the 'view' role use the update handler `createETHKeyResource` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter to change a security-relevant field of an existing object (owner, URL, token, role, chain) that creation would have rejected?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `createETHKeyResource`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: chain id and enable/disable flags (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: PATCH `chain id and enable/disable flags` with the elevated field.
- Invariant to test: update must revalidate every field against the same policy as create
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test patching security-relevant fields
