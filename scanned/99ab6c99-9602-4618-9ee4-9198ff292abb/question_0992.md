# Q0992: secret field serialized in eth_key.SetETHKeyEthBalance

## Question
Does the resource built by `SetETHKeyEthBalance` for the JSON:API response of /v2/keys/evm and the ETH key formatter middleware include a secret field (private key, seed, token, password, DSN, share) that an authenticated node user holding only the 'view' role can read?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyEthBalance`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: the address requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `address requested` and inspect the JSON:API attributes.
- Invariant to test: presenters must whitelist non-secret attributes only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test over the presenter output
