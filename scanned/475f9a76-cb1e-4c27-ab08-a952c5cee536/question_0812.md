# Q0812: WebAuthn registration bound to the wrong user in user_controller.Index

## Question
Can an authenticated node user holding only the 'view' role register a credential through `Index` at /v2/users and /v2/user/* (password change, API token create/delete) that becomes attached to another user's account, giving permanent MFA-satisfying access?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: role value in the request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `role value in the request` with a user handle or session store cookie referring to a different account.
- Invariant to test: the registered credential must attach to the authenticated session's user only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the stored credential's user id equals the session user
