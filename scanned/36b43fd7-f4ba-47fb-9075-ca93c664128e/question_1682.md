# Q1682: deletion enables silent takeover in keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role delete or disable an object through `Index` at /v2/keys/:keyType Index/Export/Import/Delete routes and recreate it with attacker-controlled contents under the same name, so existing jobs silently use it?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Index`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the keyType path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Delete then recreate via `keyType path parameter`.
- Invariant to test: recreation must not inherit references from a deleted object without revalidation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test deleting and recreating a referenced object
