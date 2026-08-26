# Q3509: identifier-to-object confusion across types in eth_keys_controller.createETHKeyResource

## Question
Can an authenticated node user holding only the 'view' role supply an identifier of the wrong type/namespace at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter so `createETHKeyResource` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `createETHKeyResource`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: the address path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `address path parameter` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
