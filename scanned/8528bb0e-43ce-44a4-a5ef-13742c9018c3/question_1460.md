# Q1460: spec presenter echoes credentials in eth_key.SetETHKeyEthBalance

## Question
Does the spec rendered by `SetETHKeyEthBalance` at the JSON:API response of /v2/keys/evm and the ETH key formatter middleware include embedded credentials (bridge tokens, URLs with basic auth, initiator secrets, webhook tokens) submitted at creation time?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyEthBalance`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: the address requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create an object with a credential-bearing field then fetch `address requested`.
- Invariant to test: credential-bearing spec fields must be redacted on read
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: round-trip test creating with credentials and asserting redaction on read
