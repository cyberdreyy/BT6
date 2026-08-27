# Q3102: WebAuthn registration bound to the wrong user in authentication.BasicAdminUsersORM

## Question
Can an unauthenticated HTTP client that can reach the node API port register a credential through `BasicAdminUsersORM` at POST /sessions and every AuthenticationProvider call behind /v2 auth that becomes attached to another user's account, giving permanent MFA-satisfying access?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `BasicAdminUsersORM`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: session id presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `session id presented` with a user handle or session store cookie referring to a different account.
- Invariant to test: the registered credential must attach to the authenticated session's user only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the stored credential's user id equals the session user
