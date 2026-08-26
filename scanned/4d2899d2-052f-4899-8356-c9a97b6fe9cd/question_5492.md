# Q5492: deletion enables silent takeover in external_initiators_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) delete or disable an object through `Create` at POST/DELETE /v2/external_initiators and recreate it with attacker-controlled contents under the same name, so existing jobs silently use it?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `Create`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: the initiator name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Delete then recreate via `initiator name and URL`.
- Invariant to test: recreation must not inherit references from a deleted object without revalidation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test deleting and recreating a referenced object
