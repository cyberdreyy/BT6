# Q2425: error/status fields carry raw upstream output in eth_key.SetETHKeyLinkBalance

## Question
Does `SetETHKeyLinkBalance` include raw upstream errors or task results at the JSON:API response of /v2/keys/evm and the ETH key formatter middleware that contain secrets or internal endpoints readable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyLinkBalance`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: chain id filter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger a failing run then fetch `chain id filter`.
- Invariant to test: rendered errors must be sanitized
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting rendered error fields are sanitized
