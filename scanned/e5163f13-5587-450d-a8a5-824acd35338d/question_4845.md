# Q4845: password change without old-password proof in user.ValidateAndHashPassword

## Question
Can an unauthenticated HTTP client that can reach the node API port change the password (or set a new one) through the path reaching `ValidateAndHashPassword` at POST /sessions and PATCH /v2/user/password without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateAndHashPassword`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: password bytes and length (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `password bytes and length` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
