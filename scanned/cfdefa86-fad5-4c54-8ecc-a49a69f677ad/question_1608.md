# Q1608: update path widens privileges of an existing object in workflow_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role use the update handler `Index` at GET /v2/keys/workflow to change a security-relevant field of an existing object (owner, URL, token, role, chain) that creation would have rejected?

## Target
- File/function: [core/web/workflow_keys_controller.go](core/web/workflow_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/workflow
- Attacker controls: the request path and query parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: PATCH `request path and query parameters` with the elevated field.
- Invariant to test: update must revalidate every field against the same policy as create
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test patching security-relevant fields
