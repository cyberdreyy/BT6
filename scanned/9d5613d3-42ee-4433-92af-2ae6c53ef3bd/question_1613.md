# Q1613: update path widens privileges of an existing object in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role use the update handler `Patch` at GET and PATCH /v2/log to change a security-relevant field of an existing object (owner, URL, token, role, chain) that creation would have rejected?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: logLevel and sqlEnabled fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: PATCH `logLevel and sqlEnabled fields` with the elevated field.
- Invariant to test: update must revalidate every field against the same policy as create
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test patching security-relevant fields
