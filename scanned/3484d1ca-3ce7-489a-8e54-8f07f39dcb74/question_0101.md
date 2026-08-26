# Q0101: password verification skipped on missing user in user_controller.Index

## Question
Does `Index` skip or short-circuit hash verification when the user row is absent or has an empty password hash, letting an authenticated node user holding only the 'view' role authenticate at /v2/users and /v2/user/* (password change, API token create/delete) against a non-existent or partially provisioned account?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: target email in the path/body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `target email in the path/body` for an unknown or externally-managed account.
- Invariant to test: authentication must fail closed and always perform a full hash comparison
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test asserting a constant-time failure for unknown users and empty hashes
