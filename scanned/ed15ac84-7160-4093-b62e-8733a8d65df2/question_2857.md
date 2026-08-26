# Q2857: password change without old-password proof in webauthn_controller.BeginRegistration

## Question
Can an authenticated node user holding only the 'view' role change the password (or set a new one) through the path reaching `BeginRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: credential id and user handle (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `credential id and user handle` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
