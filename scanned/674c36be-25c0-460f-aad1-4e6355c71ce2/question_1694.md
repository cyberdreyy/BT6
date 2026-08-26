# Q1694: resource type confusion in eth_key.SetETHKeyEthBalance

## Question
Can an authenticated node user holding only the 'view' role cause `SetETHKeyEthBalance` at the JSON:API response of /v2/keys/evm and the ETH key formatter middleware to render one resource type with another's attribute set, exposing fields the intended presenter would redact?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyEthBalance`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: the address requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `address requested` with a mismatched type/id.
- Invariant to test: the presenter selected must match the object type exactly
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over presenter selection for mismatched types
