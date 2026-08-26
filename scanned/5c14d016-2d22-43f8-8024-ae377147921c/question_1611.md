# Q1611: update path widens privileges of an existing object in replay_controller.ReplayFromBlock

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) use the update handler `ReplayFromBlock` at POST /v2/replay_from_block/:number to change a security-relevant field of an existing object (owner, URL, token, role, chain) that creation would have rejected?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: the block number path parameter (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: PATCH `block number path parameter` with the elevated field.
- Invariant to test: update must revalidate every field against the same policy as create
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test patching security-relevant fields
