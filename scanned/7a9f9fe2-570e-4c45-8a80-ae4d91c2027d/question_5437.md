# Q5437: update path widens privileges of an existing object in keys_controller.Delete

## Question
Can an authenticated node user holding only the 'view' role use the update handler `Delete` at /v2/keys/:keyType Index/Export/Import/Delete routes to change a security-relevant field of an existing object (owner, URL, token, role, chain) that creation would have rejected?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Delete`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the imported key JSON and its password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: PATCH `imported key JSON and its password` with the elevated field.
- Invariant to test: update must revalidate every field against the same policy as create
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test patching security-relevant fields
