# Q2858: password change without old-password proof in user_controller.Create

## Question
Can an authenticated node user holding only the 'view' role change the password (or set a new one) through the path reaching `Create` at /v2/users and /v2/user/* (password change, API token create/delete) without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: role value in the request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `role value in the request` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
