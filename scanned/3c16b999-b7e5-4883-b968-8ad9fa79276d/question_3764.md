# Q3764: response includes other users' objects in evm_transfer_controller.CreateWithRelayer

## Question
Does the listing produced by `CreateWithRelayer` at POST /v2/transfers/evm include records outside an authenticated node user holding only the 'edit' role (non-admin)'s entitlement (other users, other initiators, other owners)?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: from/to addresses (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `from/to addresses` and compare returned ids to the caller's scope.
- Invariant to test: listings must be filtered by the caller's entitlement
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing listing contents across roles
