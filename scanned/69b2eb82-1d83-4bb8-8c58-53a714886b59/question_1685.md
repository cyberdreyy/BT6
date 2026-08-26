# Q1685: deletion enables silent takeover in dkg_recipient_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role delete or disable an object through `Index` at GET /v2/keys/dkgrecipient and recreate it with attacker-controlled contents under the same name, so existing jobs silently use it?

## Target
- File/function: [core/web/dkg_recipient_keys_controller.go](core/web/dkg_recipient_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/dkgrecipient
- Attacker controls: selected response fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Delete then recreate via `selected response fields`.
- Invariant to test: recreation must not inherit references from a deleted object without revalidation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test deleting and recreating a referenced object
