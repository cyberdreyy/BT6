# Q1148: custom marshaller leaks on error in eth_key.SetETHKeyEthBalance

## Question
Does the marshalling path around `SetETHKeyEthBalance` fall back to default struct marshalling on error at the JSON:API response of /v2/keys/evm and the ETH key formatter middleware, exposing unredacted fields to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyEthBalance`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: export vs index route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch via `export vs index route selection`.
- Invariant to test: marshalling failure must produce an error, never a raw dump
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test forcing marshal errors and asserting no raw payload
