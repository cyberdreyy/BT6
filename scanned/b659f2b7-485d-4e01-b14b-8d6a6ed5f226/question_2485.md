# Q2485: secret returned in the success response in eth_keys_controller.createETHKeyResource

## Question
Does the response produced by `createETHKeyResource` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter include key material, export bundles, passwords, tokens or bridge/EI secrets readable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `createETHKeyResource`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: the address path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `address path parameter` and inspect every field of the response.
- Invariant to test: responses must never carry secret material to a non-owner or low-role caller
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the response body matches a redacted golden fixture
