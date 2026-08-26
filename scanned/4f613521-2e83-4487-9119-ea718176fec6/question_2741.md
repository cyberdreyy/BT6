# Q2741: spec fields reach outbound requests with node credentials in eth_keys_controller.createETHKeyResource

## Question
Can an authenticated node user holding only the 'view' role point a URL/host field accepted by `createETHKeyResource` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter at an internal address or attacker host so the node performs a request carrying its own credentials or secrets?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `createETHKeyResource`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: the address path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `address path parameter` with an internal or attacker URL.
- Invariant to test: outbound targets from user-supplied specs must be validated and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the URL validator with internal/attacker targets
