# Q0484: password change without old-password proof in authentication.AuthenticationProviderName

## Question
Can an unauthenticated HTTP client that can reach the node API port change the password (or set a new one) through the path reaching `AuthenticationProviderName` at POST /sessions and every AuthenticationProvider call behind /v2 auth without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProviderName`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: session id presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `session id presented` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
