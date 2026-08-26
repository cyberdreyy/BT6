# Q4982: state change without authorization ordering in eth_keys_controller.formatETHKeyResponse

## Question
Does `formatETHKeyResponse` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter mutate state before completing its authorization or validation, so an authenticated node user holding only the 'view' role gets the effect together with the error?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `formatETHKeyResponse`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: the address path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `address path parameter` that fails late.
- Invariant to test: no state change may precede a completed authorization
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test asserting no mutation accompanies an error response
