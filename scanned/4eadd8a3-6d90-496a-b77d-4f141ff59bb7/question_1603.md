# Q1603: update path widens privileges of an existing object in bridge_types_controller.ValidateBridgeTypeNotExist

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) use the update handler `ValidateBridgeTypeNotExist` at POST/PATCH/GET /v2/bridge_types to change a security-relevant field of an existing object (owner, URL, token, role, chain) that creation would have rejected?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeTypeNotExist`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: confirmations and minimum contract payment (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: PATCH `confirmations and minimum contract payment` with the elevated field.
- Invariant to test: update must revalidate every field against the same policy as create
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test patching security-relevant fields
