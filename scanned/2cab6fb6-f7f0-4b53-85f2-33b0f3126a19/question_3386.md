# Q3386: listing renders objects across owners in eth_key.SetETHKeyMaxGasPriceWei

## Question
Does the collection built by `SetETHKeyMaxGasPriceWei` at the JSON:API response of /v2/keys/evm and the ETH key formatter middleware render objects outside an authenticated node user holding only the 'view' role's entitlement together with their sensitive attributes?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyMaxGasPriceWei`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: chain id filter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `chain id filter` as a low-role user.
- Invariant to test: collections must be filtered before rendering
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing collection contents per role
