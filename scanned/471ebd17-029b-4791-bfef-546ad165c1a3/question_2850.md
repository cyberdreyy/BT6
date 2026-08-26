# Q2850: password change without old-password proof in orm.FindUser

## Question
Can an unauthenticated HTTP client that can reach the node API port change the password (or set a new one) through the path reaching `FindUser` at POST /sessions, API-token auth headers and session cookie lookup without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUser`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: session id in the cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `session id in the cookie` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
