# Q3636: update path widens privileges of an existing object in csa_keys_controller.Create

## Question
Can an authenticated node user holding only the 'view' role use the update handler `Create` at /v2/keys/csa and /v2/keys/csa/export/:ID to change a security-relevant field of an existing object (owner, URL, token, role, chain) that creation would have rejected?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: imported key material (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: PATCH `imported key material` with the elevated field.
- Invariant to test: update must revalidate every field against the same policy as create
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test patching security-relevant fields
