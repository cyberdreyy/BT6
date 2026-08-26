# Q3813: login race creates duplicate identity in user_controller.Create

## Question
Can concurrent requests to /v2/users and /v2/user/* (password change, API token create/delete) racing inside `Create` create two sessions or two user rows for one identity, so an authenticated node user holding only the 'view' role keeps a session that the operator cannot see or revoke?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: target email in the path/body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `target email in the path/body`.
- Invariant to test: session and user creation must be serialized and idempotent per identity
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: concurrent test asserting a single row/session results
