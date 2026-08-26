# Q4013: chain id selects an unauthorized keystore in eth_keys_controller.createETHKeyResource

## Question
Can an authenticated node user holding only the 'view' role pick a chain identifier at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter that makes `createETHKeyResource` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `createETHKeyResource`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: the address path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `address path parameter` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
