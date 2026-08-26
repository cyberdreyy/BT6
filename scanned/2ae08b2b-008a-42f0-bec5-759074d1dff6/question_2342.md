# Q2342: token deletion does not revoke in user_controller.Index

## Question
Does deleting an API token or session through `Index` at /v2/users and /v2/user/* (password change, API token create/delete) leave it usable in a cache or replica, so an authenticated node user holding only the 'view' role's revoked credential still authenticates?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: role value in the request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Use `role value in the request` immediately after deletion.
- Invariant to test: revocation must be immediate and cache-coherent
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a credential right after deletion
