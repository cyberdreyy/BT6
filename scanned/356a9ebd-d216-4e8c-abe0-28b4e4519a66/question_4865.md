# Q4865: transfer parameters under-validated in eth_keys_controller.formatETHKeyResponse

## Question
Can an authenticated node user holding only the 'view' role cause `formatETHKeyResponse` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter to send funds from a node-held key by controlling destination, amount, chain or balance-check flags?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `formatETHKeyResponse`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: chain id and enable/disable flags (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `chain id and enable/disable flags` with an attacker destination and a flag that skips the balance guard.
- Invariant to test: value transfers require admin authority and must validate destination, amount and chain
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test submitting a transfer from a non-admin session
