# Q5551: response includes other users' objects in keys_controller.Delete

## Question
Does the listing produced by `Delete` at /v2/keys/:keyType Index/Export/Import/Delete routes include records outside an authenticated node user holding only the 'view' role's entitlement (other users, other initiators, other owners)?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Delete`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the export password query parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `export password query parameter` and compare returned ids to the caller's scope.
- Invariant to test: listings must be filtered by the caller's entitlement
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing listing contents across roles
