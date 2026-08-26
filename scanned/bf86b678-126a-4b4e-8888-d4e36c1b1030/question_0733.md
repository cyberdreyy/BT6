# Q0733: WebAuthn assertion not bound to the challenge in user_controller.Index

## Question
Can an authenticated node user holding only the 'view' role replay or forge the assertion validated by `Index` at /v2/users and /v2/user/* (password change, API token create/delete) because the challenge, origin or user handle is not bound to the session being authenticated?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: target email in the path/body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `target email in the path/body` captured from another login or another user.
- Invariant to test: an assertion must match the freshly issued challenge, RP origin and the authenticating user handle
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test replaying an assertion across sessions and users
