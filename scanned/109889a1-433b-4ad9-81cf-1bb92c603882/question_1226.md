# Q1226: redaction applied only on one route in eth_key.SetETHKeyEthBalance

## Question
Is redaction in `SetETHKeyEthBalance` applied on the index route but not on show/export/create at the JSON:API response of /v2/keys/evm and the ETH key formatter middleware, letting an authenticated node user holding only the 'view' role read the secret through the other route?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyEthBalance`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: the address requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare `address requested` across all routes rendering the same resource.
- Invariant to test: redaction must be a property of the resource, not of one route
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test comparing the field set across routes
