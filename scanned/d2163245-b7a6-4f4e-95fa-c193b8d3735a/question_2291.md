# Q2291: identifier reveals sensitive identity in eth_key.SetETHKeyLinkBalance

## Question
Does the identifier or metadata rendered by `SetETHKeyLinkBalance` at the JSON:API response of /v2/keys/evm and the ETH key formatter middleware reveal key identities, addresses or credential fingerprints that let an authenticated node user holding only the 'view' role target key theft or fund movement?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyLinkBalance`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: export vs index route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `export vs index route selection` at the lowest role.
- Invariant to test: identity metadata must be limited to what the caller's role needs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing rendered identifiers per role
