# Q1679: deletion enables silent takeover in jobs_controller.Index

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) delete or disable an object through `Index` at POST/PATCH /v2/jobs (edit role) and recreate it with attacker-controlled contents under the same name, so existing jobs silently use it?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Index`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: spec type and pipeline DAG (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Delete then recreate via `spec type and pipeline DAG`.
- Invariant to test: recreation must not inherit references from a deleted object without revalidation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test deleting and recreating a referenced object
