# Q1070: struct embedding pulls in secret fields in eth_key.SetETHKeyEthBalance

## Question
Does `SetETHKeyEthBalance` embed a domain struct so newly added secret fields are serialized automatically at the JSON:API response of /v2/keys/evm and the ETH key formatter middleware without anyone reviewing the response shape?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyEthBalance`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: chain id filter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `chain id filter` and compare fields against the intended resource contract.
- Invariant to test: presenters must copy explicit fields rather than embed domain structs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting the presenter's field set equals an explicit allowlist
