# Q5552: response includes other users' objects in eth_keys_controller.formatETHKeyResponse

## Question
Does the listing produced by `formatETHKeyResponse` at /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter include records outside an authenticated node user holding only the 'view' role's entitlement (other users, other initiators, other owners)?

## Target
- File/function: [core/web/eth_keys_controller.go](core/web/eth_keys_controller.go) -> `formatETHKeyResponse`
- Entrypoint: /v2/keys/evm (Index/Create/Export/Import/Chain) and the ETH key response formatter
- Attacker controls: chain id and enable/disable flags (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `chain id and enable/disable flags` and compare returned ids to the caller's scope.
- Invariant to test: listings must be filtered by the caller's entitlement
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing listing contents across roles
